"""Reminding members who have not finished the wird yet."""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes, JobQueue

from ..db.models import DailyTask, Group, TaskStatus
from ..db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..messages import render
from ..tg.failures import deactivate_if_forbidden
from ..tg.mentions import chunked

log = logging.getLogger(__name__)

REMIND_JOB = "remind"

# A single missed day is a normal slip, not تقصير. The notice starts once someone
# has missed two days in a row, so the bot encourages rather than nags.
MISSED_NOTICE_THRESHOLD = 2


def in_quiet_hours(now: dt.time, start: dt.time | None, end: dt.time | None) -> bool:
    """Whether `now` falls in the group's do-not-disturb window.

    The window normally wraps midnight (23:00 to 07:00), so the comparison is
    split rather than a plain `start <= now < end`.
    """
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def reminder_times(group: Group, sent_at: dt.datetime) -> list[dt.datetime]:
    """UTC moments for every reminder of a wird sent at `sent_at` (UTC)."""
    first = group.first_reminder_after_hours
    step = group.reminder_interval_hours
    return [sent_at + dt.timedelta(hours=first + step * i) for i in range(group.reminder_max_count)]


def schedule_task_reminders(
    job_queue: JobQueue, group: Group, task: DailyTask, *, now: dt.datetime | None = None
) -> int:
    """Schedule the still-future reminders for one wird. Returns how many.

    Reminders in the past are not scheduled: after a restart the ones that
    already fired are skipped here, and `reminder_log` guards the rest.
    """
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    scheduled = 0

    for index, when in enumerate(reminder_times(group, task.sent_at), start=1):
        if when <= now:
            continue
        job_queue.run_once(
            job_remind,
            when=when.replace(tzinfo=dt.UTC),
            chat_id=task.chat_id,
            data={"task_id": task.id, "seq": index},
            name=f"{REMIND_JOB}:{task.chat_id}:{task.id}:{index}",
        )
        scheduled += 1

    return scheduled


async def send_reminder(bot: Bot, deps: Deps, chat_id: int, task_id: int, seq: int) -> int:
    """Send one round of reminders. Returns the number of messages sent."""
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
        if group is None or not group.is_active:
            return 0

        tasks = TaskRepo(session)
        task = await tasks.get(task_id)
        if task is None or task.chat_id != chat_id or task.status is TaskStatus.CLOSED:
            return 0

        local_now = dt.datetime.now(ZoneInfo(group.timezone)).time()
        if in_quiet_hours(local_now, group.quiet_hours_start, group.quiet_hours_end):
            log.info("chat %s: reminder %s falls in quiet hours; skipped", chat_id, seq)
            return 0

        # Claim this reminder before sending. If a restart replays the job, the
        # second attempt finds the row and sends nothing.
        if not await tasks.log_reminder(task_id, seq):
            log.info("chat %s: reminder %s already sent", chat_id, seq)
            return 0

        pending = list(await tasks.pending_subscribers(task_id))
        if not pending:
            log.info("chat %s: everyone finished; no reminder needed", chat_id)
            return 0

        done_ids = set(await tasks.done_user_ids(task_id))
        subscribers = await SubscriberRepo(session).list_active(chat_id)
        done_names = [s.display_name for s in subscribers if s.user_id in done_ids]

        stats_repo = StatsRepo(session)
        missed = {}
        for member in pending:
            stats = await stats_repo.get(chat_id, member.user_id)
            if stats and stats.consecutive_missed >= MISSED_NOTICE_THRESHOLD:
                missed[member.user_id] = stats.consecutive_missed

        batches = [
            render.reminder_message(
                seq=seq,
                chat_id=chat_id,
                pages=(task.page_start, task.page_end),
                batch=batch,
                missed=missed,
                done_names=done_names,
                pending_total=len(pending),
                subscriber_total=len(subscribers),
            )
            for batch in chunked(pending, group.mentions_per_message)
        ]
        message_ids: list[int] = []

    for text in batches:
        try:
            message = await bot.send_message(chat_id, text)
            message_ids.append(message.message_id)
        except Forbidden:
            # The bot is no longer in the group; the caller deactivates it
            # rather than sending the remaining batches into a closed door.
            raise
        except TelegramError:
            log.exception("chat %s: a reminder message failed", chat_id)

    log.info("chat %s: reminder %s sent in %s message(s)", chat_id, seq, len(message_ids))
    return len(message_ids)


async def job_remind(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    data = job.data or {}
    deps = get_deps(context)
    try:
        await send_reminder(context.bot, deps, job.chat_id, data["task_id"], data["seq"])
    except TelegramError as exc:
        if not await deactivate_if_forbidden(deps, job.chat_id, exc):
            log.exception("chat %s: reminder job failed", job.chat_id)
