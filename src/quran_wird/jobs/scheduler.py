"""Per-group job scheduling.

Jobs live only in memory, so every one of them is rebuilt from the database on
startup. Anything that must not fire twice after a restart is guarded in the
database instead (see `TaskRepo.log_reminder` and the same-day check in
`send_wird`).
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application, JobQueue

from ..db.models import Group
from ..db.repo import GroupRepo, TaskRepo
from ..db.session import session_scope
from ..deps import DEPS_KEY, Deps
from .close_day import CLOSE_JOB, job_close_day
from .remind import REMIND_JOB, schedule_task_reminders
from .send_daily import job_send_daily

log = logging.getLogger(__name__)

SEND_JOB = "send"


def to_ptb_weekdays(weekdays: list[int]) -> tuple[int, ...]:
    """Convert Python weekdays to the convention JobQueue.run_daily expects.

    `date.weekday()` is 0=Monday..6=Sunday, which is what the database stores.
    PTB's `run_daily(days=...)` is 0=Sunday..6=Saturday (changed in PTB 20).
    Without this shift every group would read on the wrong days.
    """
    return tuple(sorted((day + 1) % 7 for day in weekdays))


def job_name(kind: str, chat_id: int) -> str:
    return f"{kind}:{chat_id}"


def clear_group_jobs(job_queue: JobQueue, chat_id: int) -> None:
    for kind in (SEND_JOB, CLOSE_JOB):
        for job in job_queue.jobs(pattern=f"^{kind}:{chat_id}$"):
            job.schedule_removal()
    # Reminder job names carry the task id and sequence too.
    for job in job_queue.jobs(pattern=f"^{REMIND_JOB}:{chat_id}:"):
        job.schedule_removal()


def schedule_group(job_queue: JobQueue, group: Group) -> None:
    """(Re)schedule every job for one group."""
    clear_group_jobs(job_queue, group.chat_id)

    if not group.is_active or not group.active_weekdays:
        log.debug("chat %s: inactive or no active weekdays; nothing scheduled", group.chat_id)
        return

    tz = ZoneInfo(group.timezone)
    send_at = dt.time(
        hour=group.send_time.hour,
        minute=group.send_time.minute,
        tzinfo=tz,
    )

    job_queue.run_daily(
        job_send_daily,
        time=send_at,
        days=to_ptb_weekdays(group.active_weekdays),
        chat_id=group.chat_id,
        name=job_name(SEND_JOB, group.chat_id),
    )

    # Deliberately every day, with no `days=` filter: a wird sent on the group's
    # last active day of the week still has to close that night, even if the
    # group has that weekday switched off.
    job_queue.run_daily(
        job_close_day,
        time=dt.time(
            hour=group.day_close_time.hour,
            minute=group.day_close_time.minute,
            tzinfo=tz,
        ),
        chat_id=group.chat_id,
        name=job_name(CLOSE_JOB, group.chat_id),
    )
    log.info(
        "chat %s: wird scheduled at %s %s on weekdays %s, closing at %s",
        group.chat_id,
        group.send_time.strftime("%H:%M"),
        group.timezone,
        group.active_weekdays,
        group.day_close_time.strftime("%H:%M"),
    )


async def reschedule_all(app: Application) -> int:
    """Rebuild jobs for every active group. Returns how many were scheduled."""
    deps: Deps = app.bot_data[DEPS_KEY]
    if app.job_queue is None:
        log.error("no job queue on this application; nothing scheduled")
        return 0

    async with session_scope(deps.sessions) as session:
        groups = list(await GroupRepo(session).list_active())

    reminders = 0
    async with session_scope(deps.sessions) as session:
        tasks = TaskRepo(session)
        by_chat = {g.chat_id: g for g in groups}
        for group in groups:
            schedule_group(app.job_queue, group)
        # An open wird from before the restart still owes its reminders; the
        # ones already sent are filtered by time here and by reminder_log later.
        for task in await tasks.list_open():
            group = by_chat.get(task.chat_id)
            if group is not None:
                reminders += schedule_task_reminders(app.job_queue, group, task)

    log.info("scheduled jobs for %s group(s), %s pending reminder(s)", len(groups), reminders)
    return len(groups)


async def reschedule_chat(app: Application, chat_id: int) -> None:
    """Re-read one group's settings and rebuild its jobs, after a settings change."""
    deps: Deps = app.bot_data[DEPS_KEY]
    if app.job_queue is None:
        return
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
    if group is not None:
        schedule_group(app.job_queue, group)
