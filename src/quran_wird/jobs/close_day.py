"""Closing the day: stats, page advancement, and the khatmah rollover.

This is the last step of the daily cycle (send → remind → close). It runs at
`day_close_time` on *every* day, not only the group's active ones, so a wird
sent on the last active day of the week still closes that night.

Closing is guarded by the task's own status rather than by the job: `get_open`
returns nothing once the wird is closed, so a replayed job after a restart
cannot count a missed day twice.
"""

from __future__ import annotations

import datetime as dt
import logging

from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..db.models import Group
from ..db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain.progress import advance_from, next_range, should_advance
from ..domain.schemas import DaySummaryView, PageRange
from ..messages import render
from ..messages.phrases import KHATMAH_DONE, KHATMAH_DUA

log = logging.getLogger(__name__)

CLOSE_JOB = "close"


def khatmah_days(group: Group, on: dt.date) -> int:
    """How long the khatmah took, counting the first and last day.

    Groups created before the field existed have no start date, so the day the
    bot first met them is the honest fallback.
    """
    started = group.khatmah_started_on or group.created_at.date()
    return max(1, (on - started).days + 1)


async def close_day(bot: Bot, deps: Deps, chat_id: int) -> int | None:
    """Close the group's open wird. Returns the closed task id, or None.

    Everything that touches the database happens in one transaction; the
    messages go out afterwards, so a slow Telegram call never holds the wird
    open.
    """
    summary: str | None = None
    khatmah_message: str | None = None

    async with session_scope(deps.sessions) as session:
        groups = GroupRepo(session)
        group = await groups.get(chat_id)
        if group is None or not group.is_active:
            return None

        tasks = TaskRepo(session)
        task = await tasks.get_open(chat_id)
        if task is None:
            log.debug("chat %s: no open wird to close", chat_id)
            return None

        subscribers = list(await SubscriberRepo(session).list_active(chat_id))
        done_ids = set(await tasks.done_user_ids(task.id))

        # Guests who pressed the button count for nothing here: they are not
        # expected daily, so they neither earn a streak nor break one, and the
        # advance rules are measured against the subscriber list.
        finishers = [s for s in subscribers if s.user_id in done_ids]
        pending = [s for s in subscribers if s.user_id not in done_ids]

        stats = StatsRepo(session)
        for member in finishers:
            # Already recorded when they pressed; record_done is idempotent per
            # date, so this only catches completions made before they subscribed.
            await stats.record_done(chat_id, member.user_id, task.task_date)
        for member in pending:
            await stats.record_missed(chat_id, member.user_id)

        advanced = should_advance(group.advance_rule, len(finishers), len(subscribers))
        khatmah_completed = False
        if advanced:
            next_page, khatmah_completed = advance_from(task.page_end)
            if khatmah_completed:
                khatmah_message = KHATMAH_DONE.format(
                    khatmah=render.ar_num(group.khatmah_number),
                    days=render.ar_num(khatmah_days(group, task.task_date)),
                )
                # The new khatmah begins with tomorrow's wird, not tonight.
                await groups.start_new_khatmah(chat_id, on=task.task_date + dt.timedelta(days=1))
            else:
                await groups.set_current_page(chat_id, next_page)

        await tasks.close(task.id)

        top = await stats.top_streak(chat_id)
        names = {s.user_id: s.display_name for s in subscribers}
        summary = render.day_summary(
            DaySummaryView(
                task_date=task.task_date,
                pages=PageRange(start=task.page_start, end=task.page_end),
                done_names=[s.display_name for s in finishers],
                subscriber_count=len(subscribers),
                advanced=advanced,
                next_pages=next_range(group.current_page, group.pages_per_day),
                top_streak_name=names.get(top.user_id) if top else None,
                top_streak_days=top.current_streak if top else 0,
                khatmah_completed=khatmah_completed,
            )
        )
        task_id = task.id
        pages_advanced_to = group.current_page

    await _announce(bot, chat_id, summary, khatmah_message)

    log.info(
        "chat %s: closed task %s; next wird starts at page %s",
        chat_id,
        task_id,
        pages_advanced_to,
    )
    return task_id


async def _announce(bot: Bot, chat_id: int, summary: str, khatmah_message: str | None) -> None:
    """Post the day's summary, and the khatmah announcement when there is one.

    A failed message must not undo a closed day, so every send is caught: the
    database is already correct, and the group simply misses one report.
    """
    try:
        await bot.send_message(chat_id, summary)
    except TelegramError:
        log.exception("chat %s: could not post the daily summary", chat_id)

    if khatmah_message is None:
        return

    try:
        await bot.send_message(chat_id, khatmah_message)
        # The dua carries tashkeel and hard line breaks that HTML parsing would
        # damage, so it goes out unparsed and in a message of its own.
        await bot.send_message(chat_id, KHATMAH_DUA, parse_mode=None)
    except TelegramError:
        log.exception("chat %s: could not post the khatmah announcement", chat_id)


async def job_close_day(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue entry point, one per group."""
    chat_id = context.job.chat_id
    try:
        await close_day(context.bot, get_deps(context), chat_id)
    except TelegramError:
        log.exception("chat %s: closing the day failed", chat_id)
