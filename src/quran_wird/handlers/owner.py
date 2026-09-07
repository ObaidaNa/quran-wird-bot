"""The owner panel: `/owner`, read in a private chat by whoever runs the bot.

Deliberately unlike every other command here. It answers about the whole fleet
rather than one group, so it never works inside a group — a count of all the
bot's groups is nobody's business but the operator's — and it is not published
in the command menu.

Access comes from `OWNER_IDS` in the environment. Left unset the panel answers
nobody, which is the right default for a repository anyone can clone: a fork
that forgets to configure it does not leak its numbers, it simply has no panel.
A stranger who guesses the command gets silence rather than a refusal, since
"this command exists but not for you" is itself an answer.

Like `/settings`, it is one message that rewrites itself between screens, so
there is no conversation state to lose across a restart.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from ..db.repo import OwnerStatsRepo
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain.schemas import GroupDigest, OwnerStats
from ..messages import ar
from ..messages import owner_view as view

log = logging.getLogger(__name__)

PRIVATE_FILTER = filters.ChatType.PRIVATE

# How far back "this week" and "this month" reach on the activity screen.
WEEK_DAYS = 7
MONTH_DAYS = 30


def is_owner(deps: Deps, user_id: int | None) -> bool:
    return user_id is not None and user_id in deps.settings.owners


async def gather(deps: Deps) -> tuple[OwnerStats, list[GroupDigest], list[GroupDigest]]:
    """Read the whole fleet in one transaction.

    Returns the counts, the largest groups, and the groups nearest to finishing
    their khatmah — every screen of the panel is drawn from this one pass, so
    moving between screens never re-reads the database.

    "Today" is the bot's default timezone rather than each group's: the groups
    span several, and an operator wants one number, not a set of them.
    """
    now_local = dt.datetime.now(ZoneInfo(deps.settings.default_timezone))
    today = now_local.date()
    week_start = today - dt.timedelta(days=WEEK_DAYS - 1)

    # Completion times are UTC, so the windows they are compared against must be.
    now_utc = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    day_ago = now_utc - dt.timedelta(days=1)
    week_ago = now_utc - dt.timedelta(days=WEEK_DAYS)
    month_ago = now_utc - dt.timedelta(days=MONTH_DAYS)

    async with session_scope(deps.sessions) as session:
        repo = OwnerStatsRepo(session)

        groups_total, groups_active = await repo.group_counts()
        subscriptions_total, subscriptions_active = await repo.subscription_counts()
        on_streak, longest_streak = await repo.streaks()
        days_done, days_missed = await repo.reading_totals()
        khatmahs_completed, pages_read = await repo.khatmah_totals()
        wirds_total, wirds_today, wirds_week = await repo.wird_counts(
            today=today, week_start=week_start
        )
        completions_total, completions_today, completions_week = await repo.completion_counts(
            day_ago, week_ago
        )

        stats = OwnerStats(
            generated_at=now_local.replace(tzinfo=None),
            groups_total=groups_total,
            groups_active=groups_active,
            groups_new_week=await repo.groups_created_since(week_ago),
            groups_new_month=await repo.groups_created_since(month_ago),
            groups_reading_week=await repo.groups_reading_since(week_start),
            unique_users=await repo.unique_users(),
            subscriptions_total=subscriptions_total,
            subscriptions_active=subscriptions_active,
            members_on_streak=on_streak,
            longest_streak=longest_streak,
            days_done=days_done,
            days_missed=days_missed,
            khatmahs_completed=khatmahs_completed,
            pages_read=pages_read,
            wirds_total=wirds_total,
            wirds_today=wirds_today,
            wirds_week=wirds_week,
            completions_total=completions_total,
            completions_today=completions_today,
            completions_week=completions_week,
        )
        largest = list(await repo.digests(limit=view.MAX_LISTED))
        nearest = list(await repo.digests(limit=view.MAX_NEAREST, by_progress=True))

    return stats, largest, nearest


async def screen(deps: Deps, where: str) -> tuple[str, InlineKeyboardMarkup]:
    """Draw one screen of the panel, with counts read fresh every time.

    Re-reading on every press is deliberate: the panel is opened to answer "how
    is it doing right now", and a cached number would answer a different
    question. The queries are counts over a small database.
    """
    stats, largest, nearest = await gather(deps)
    match where:
        case "groups":
            return view.groups(stats, largest)
        case "members":
            return view.members(stats)
        case "activity":
            return view.activity(stats)
        case "khatmahs":
            return view.khatmahs(stats, nearest)
        case _:
            return view.home(stats)


# ------------------------------------------------------------------ handlers


async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if message is None or user is None:
        return

    deps = get_deps(context)
    if not deps.settings.owners:
        # Not configured. Saying so in a private chat costs nothing and is the
        # only way the operator learns why their own panel is silent.
        await message.reply_text(ar.OWNER_NOT_CONFIGURED)
        return

    if not is_owner(deps, user.id):
        log.info("user %s asked for the owner panel and is not an owner", user.id)
        return

    text, keyboard = await screen(deps, "home")
    await message.reply_text(text, reply_markup=keyboard)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    deps = get_deps(context)
    # Checked on every press, not only when the panel is opened: a forwarded
    # panel would otherwise carry live buttons to whoever received it.
    if not is_owner(deps, query.from_user.id if query.from_user else None):
        await query.answer(ar.OWNER_ONLY, show_alert=True)
        return

    data = query.data.split(":", 1)[1]
    if data == "done":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            log.debug("could not clear the owner panel keyboard")
        return

    where = data.split(":", 1)[1] if data.startswith("go:") else "home"
    text, keyboard = await screen(deps, where)
    await query.answer()
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as exc:
        # "not modified" means nothing changed since the last refresh, which is
        # the ordinary answer to pressing تحديث twice.
        if "not modified" not in str(exc).lower():
            log.warning("could not redraw the owner panel (%s)", exc)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("owner", owner_command, filters=PRIVATE_FILTER))
    app.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{view.CB}:"))
