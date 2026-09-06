"""Reading the record: /me, /progress, /top, and /week.

Each command has a core function that returns finished text, so the reports can
be tested without building Telegram updates. The handlers only fetch, reply, and
stay out of the way.
"""

from __future__ import annotations

import datetime as dt
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from ..db.models import BadgeKind
from ..db.repo import (
    BadgeRepo,
    GroupRepo,
    StatsRepo,
    SubscriberRepo,
    WeeklyReportRepo,
)
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain.schemas import MemberProgress
from ..domain.weekly import week_bounds, weeks_to_finish
from ..jobs.send_daily import local_today
from ..jobs.weekly_report import collect_week
from ..messages import ar
from ..messages import stats_view as view

log = logging.getLogger(__name__)

GROUP_FILTER = filters.ChatType.GROUPS


async def member_report(deps: Deps, chat_id: int, user_id: int, *, display_name: str) -> str:
    """One member's own record."""
    async with session_scope(deps.sessions) as session:
        subscriber = await SubscriberRepo(session).get(chat_id, user_id)
        stats = await StatsRepo(session).get(chat_id, user_id)
        badges = await BadgeRepo(session).count(chat_id, user_id, BadgeKind.PERFECT_WEEK)

        if stats is None and subscriber is None:
            return ar.NO_STATS_YET.format(name=display_name)

        progress = (
            MemberProgress.model_validate(stats)
            if stats is not None
            else MemberProgress(user_id=user_id)
        )
        progress.display_name = subscriber.display_name if subscriber else display_name
        subscribed = subscriber is not None and subscriber.is_active

    return view.member_report(progress, subscribed=subscribed, badges=badges)


async def group_progress(deps: Deps, chat_id: int) -> str | None:
    """How far the group has come, and roughly how far is left."""
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
        if group is None:
            return None
        subscriber_count = await SubscriberRepo(session).count_active(chat_id)
        recent = list(await WeeklyReportRepo(session).recent(chat_id, limit=4))

    # Prefer the pace the group actually kept; fall back to its configured one
    # for a group too new to have a week behind it.
    if recent:
        pages_per_week = sum(r.pages_read for r in recent) / len(recent)
    else:
        pages_per_week = group.pages_per_day * len(group.active_weekdays or [1])

    return view.khatmah_report(
        current_page=group.current_page,
        khatmah_number=group.khatmah_number,
        started_on=group.khatmah_started_on,
        pages_per_day=group.pages_per_day,
        subscriber_count=subscriber_count,
        weeks_left=weeks_to_finish(group.current_page, round(pages_per_week)),
    )


async def leaderboard(deps: Deps, chat_id: int) -> str:
    """The standings, naming only people who are still subscribed."""
    async with session_scope(deps.sessions) as session:
        names = {
            s.user_id: s.display_name for s in await SubscriberRepo(session).list_active(chat_id)
        }
        rows = [
            MemberProgress.model_validate(stats)
            for stats in await StatsRepo(session).leaderboard(
                chat_id, limit=view.MAX_LEADERBOARD, user_ids=list(names)
            )
        ]
        for row in rows:
            row.display_name = names[row.user_id]

    return view.leaderboard(rows)


async def weekly_preview(deps: Deps, chat_id: int, *, on: dt.date | None = None) -> str | None:
    """This week's board so far, without recording that it was sent.

    /week is a look at the standings mid-week; only the Friday job claims the
    week, so asking for a preview cannot silence the real report.
    """
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
        if group is None:
            return None
        day = on or local_today(group)
        start, end = week_bounds(day, group.week_start_weekday)
        return await collect_week(session, group, start, end)


# ------------------------------------------------------------------ handlers


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or message is None:
        return
    text = await member_report(get_deps(context), chat.id, user.id, display_name=user.full_name)
    await message.reply_text(text)


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    text = await group_progress(get_deps(context), chat.id)
    await message.reply_text(text or ar.NOT_REGISTERED)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    await message.reply_text(await leaderboard(get_deps(context), chat.id))


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    text = await weekly_preview(get_deps(context), chat.id)
    await message.reply_text(text or ar.NOT_REGISTERED)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("me", me, filters=GROUP_FILTER))
    app.add_handler(CommandHandler("progress", progress, filters=GROUP_FILTER))
    app.add_handler(CommandHandler("top", top, filters=GROUP_FILTER))
    app.add_handler(CommandHandler("week", week, filters=GROUP_FILTER))
