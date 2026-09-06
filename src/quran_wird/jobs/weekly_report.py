"""The Friday honors board.

Sent once a week, in the evening, for the week that ends that day. Like every
other announcement in this bot, sending exactly once is guaranteed in the
database rather than by the job: the `weekly_reports` row is claimed before the
message goes out, so a restart on a Friday evening cannot post a second board.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

from ..db.models import BadgeKind, Group
from ..db.repo import (
    BadgeRepo,
    GroupRepo,
    StatsRepo,
    SubscriberRepo,
    TaskRepo,
    WeeklyReportRepo,
)
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain.schemas import MemberProgress
from ..domain.weekly import WeekMember, WeekTask, summarise, week_bounds, weeks_to_finish
from ..messages import stats_view as view
from ..tg.failures import deactivate_if_forbidden
from .send_daily import local_today

log = logging.getLogger(__name__)

WEEKLY_JOB = "weekly"


async def gather(
    session: AsyncSession, group: Group, start: dt.date, end: dt.date
) -> tuple[object, list[int]]:
    """Read one week out of the database. Returns (summary, perfect user ids)."""
    tasks_repo = TaskRepo(session)
    rows = await tasks_repo.list_between(group.chat_id, start, end)

    tasks = [
        WeekTask(
            task_date=task.task_date,
            page_count=task.page_count,
            done_user_ids=frozenset(await tasks_repo.done_user_ids(task.id)),
            is_repeat=task.is_repeat_of is not None,
        )
        for task in rows
    ]
    members = [
        WeekMember(
            user_id=s.user_id,
            display_name=s.display_name,
            # joined_at is stored in UTC; the week is in the group's own days.
            joined_on=s.joined_at.date(),
        )
        for s in await SubscriberRepo(session).list_active(group.chat_id)
    ]

    summary = summarise(tasks, members)
    return summary, [entry.member.user_id for entry in summary.perfect]


async def render_week(session: AsyncSession, group: Group, start: dt.date, end: dt.date) -> str:
    """Build the board's text for one week."""
    summary, _ = await gather(session, group, start, end)

    top = await StatsRepo(session).top_streak(group.chat_id)
    top_progress = None
    if top is not None:
        subscriber = await SubscriberRepo(session).get(group.chat_id, top.user_id)
        if subscriber is not None and subscriber.is_active:
            top_progress = MemberProgress.model_validate(top)
            top_progress.display_name = subscriber.display_name

    return view.weekly_report(
        chat_id=group.chat_id,
        week_start=start,
        week_end=end,
        summary=summary,
        current_page=group.current_page,
        khatmah_number=group.khatmah_number,
        top_streak=top_progress,
        weeks_left=weeks_to_finish(group.current_page, summary.pages_read),
    )


async def collect_week(session: AsyncSession, group: Group, start: dt.date, end: dt.date) -> str:
    """The board as /week shows it: rendered, but not claimed or badged."""
    return await render_week(session, group, start, end)


async def send_weekly_report(
    bot: Bot, deps: Deps, chat_id: int, *, on: dt.date | None = None
) -> bool:
    """Post the honors board for the week ending today. Returns whether it went out."""
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
        if group is None or not group.is_active or not group.weekly_report_enabled:
            return False

        day = on or local_today(group)
        start, end = week_bounds(day, group.week_start_weekday)

        reports = WeeklyReportRepo(session)
        summary, perfect_ids = await gather(session, group, start, end)

        # Claimed before sending: a replayed job finds the row and stops here.
        claimed = await reports.claim(
            chat_id,
            week_start=start,
            week_end=end,
            pages_read=summary.pages_read,
            active_days=summary.active_days,
            perfect_user_ids=perfect_ids,
        )
        if claimed is None:
            log.info("chat %s: the week of %s was already reported", chat_id, start)
            return False

        badges = BadgeRepo(session)
        for user_id in perfect_ids:
            await badges.award(chat_id, user_id, BadgeKind.PERFECT_WEEK, ref=start.isoformat())

        text = await render_week(session, group, start, end)

    try:
        message = await bot.send_message(chat_id, text)
    except Forbidden:
        # Left to the caller: the group has removed the bot, and no future
        # report will land either.
        raise
    except TelegramError:
        log.exception("chat %s: the weekly report could not be sent", chat_id)
        return False

    async with session_scope(deps.sessions) as session:
        await WeeklyReportRepo(session).set_message(chat_id, start, message.message_id)

    log.info(
        "chat %s: weekly report for %s–%s sent (%s perfect)",
        chat_id,
        start,
        end,
        len(perfect_ids),
    )
    return True


def report_time(group: Group) -> dt.time:
    return dt.time(
        hour=group.weekly_report_time.hour,
        minute=group.weekly_report_time.minute,
        tzinfo=ZoneInfo(group.timezone),
    )


async def job_weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue entry point, one per group."""
    chat_id = context.job.chat_id
    deps = get_deps(context)
    try:
        await send_weekly_report(context.bot, deps, chat_id)
    except TelegramError as exc:
        if not await deactivate_if_forbidden(deps, chat_id, exc):
            log.exception("chat %s: the weekly report job failed", chat_id)
