"""The admin settings panel: /settings, its buttons, and /setpage.

The panel is one message that rewrites itself. Every value is changed by a
button, so there is no conversation state to keep — which means a restart in the
middle of an edit costs nothing, and two admins pressing at once simply see each
other's result.

`/setpage` is the exception, and deliberately so: a group that was already part
way through a khatmah before the bot arrived needs to jump to page 350 in one
step, and no reasonable number of button presses gets there.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from telegram import InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from ..db.models import AdvanceRule, Group
from ..db.repo import GroupRepo, MushafRepo, SubscriberRepo
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain.schemas import GroupSettingsPatch
from ..domain.settings import NUMERIC, TIME_ATTRS, bump_int, bump_time, parse_page, toggle_weekday
from ..jobs.scheduler import reschedule_chat
from ..messages import ar
from ..messages import settings_view as view
from ..tg.guards import is_group_admin

log = logging.getLogger(__name__)

GROUP_FILTER = filters.ChatType.GROUPS

# Changing any of these moves a scheduled job, so the group's jobs are rebuilt.
SCHEDULED_ATTRS = frozenset(
    {"send_time", "day_close_time", "active_weekdays", "timezone", "is_active"}
)

SCREENS = {
    "pages": view.pages,
    "send": view.send,
    "close": view.close_time,
    "days": view.days,
    "rem": view.reminders,
    "quiet": view.quiet,
    "tz": view.timezone,
    "advance": view.advance,
    "weekly": view.weekly,
    "wstart": view.week_start,
    "page": view.page,
    "pause": view.pause,
    "khatmah": view.khatmah,
}

# Screens that need more than the group row to draw.
SUBSCRIBERS_SCREEN = "subs"


@dataclass
class Screen:
    """What the panel should show next, and whether the jobs must be rebuilt."""

    text: str
    keyboard: InlineKeyboardMarkup
    reschedule: bool = False


def _screen(
    group: Group,
    where: str,
    subscriber_count: int,
    *,
    members: list | None = None,
    reschedule: bool = False,
) -> Screen:
    if where == SUBSCRIBERS_SCREEN:
        text, keyboard = view.subscribers(group, members or [])
    elif where == "home" or where not in SCREENS:
        text, keyboard = view.home(group, subscriber_count)
    else:
        text, keyboard = SCREENS[where](group)
    return Screen(text, keyboard, reschedule)


def _patch(group: Group, attr: str, value: object) -> GroupSettingsPatch | None:
    """Validate one field the same way the panel-free path would.

    Returning None means the new value is out of bounds and the press is simply
    ignored, which is what a stepper at the end of its range should do.
    """
    try:
        return GroupSettingsPatch(**{attr: value})
    except ValueError:
        log.warning("chat %s: rejected %s=%r", group.chat_id, attr, value)
        return None


async def apply(deps: Deps, chat_id: int, data: str) -> Screen | None:
    """Act on one panel button. Returns the screen to draw, or None to redraw nothing.

    `data` is the callback payload without its `cfg:` prefix.
    """
    async with session_scope(deps.sessions) as session:
        groups = GroupRepo(session)
        group = await groups.get(chat_id)
        if group is None:
            return None

        parts = data.split(":")
        where = "home"
        reschedule = False
        patch: GroupSettingsPatch | None = None

        match parts:
            case ["noop"] | ["home"]:
                pass

            case ["go", target]:
                where = target

            case ["n", key, delta] if key in NUMERIC:
                field = NUMERIC[key]
                current = getattr(group, field.attr)
                new = bump_int(current, int(delta), lo=field.lo, hi=field.hi)
                patch = _patch(group, field.attr, new)
                where = _origin_of(key)

            case ["t", key, minutes] if key in TIME_ATTRS:
                attr = TIME_ATTRS[key]
                current = getattr(group, attr) or dt.time(0, 0)
                patch = _patch(group, attr, bump_time(current, int(minutes)))
                where = _origin_of(key)

            case ["d", day]:
                days = toggle_weekday(group.active_weekdays, int(day))
                patch = _patch(group, "active_weekdays", days)
                where = "days"

            case ["w", day]:
                patch = _patch(group, "weekly_report_weekday", int(day))
                where = "weekly"

            case ["s", day]:
                patch = _patch(group, "week_start_weekday", int(day))
                where = "wstart"

            case ["tz", *rest] if (name := ":".join(rest)) in view.CITY_BY_TZ:
                patch = _patch(group, "timezone", name)
                where = "tz"

            case ["a", rule] if rule in {r.value for r in AdvanceRule}:
                patch = _patch(group, "advance_rule", AdvanceRule(rule))
                where = "advance"

            case ["quiet", state]:
                # Disabling clears both ends; enabling restores the defaults,
                # since a quiet window with only one end is meaningless.
                on = state == "on"
                group.quiet_hours_start = dt.time(23, 0) if on else None
                group.quiet_hours_end = dt.time(7, 0) if on else None
                where = "quiet"

            case ["khatmah", "new"]:
                # Deliberately not reachable in one press: the confirm screen is
                # the guard, since this resets a khatmah the whole group shares.
                await groups.start_new_khatmah(chat_id)
                where = "khatmah"

            case ["sub", user_id]:
                await SubscriberRepo(session).unsubscribe(chat_id, int(user_id))
                where = SUBSCRIBERS_SCREEN

            case ["we"]:
                group.weekly_report_enabled = not group.weekly_report_enabled
                where = "weekly"

            case ["pause", state]:
                patch = _patch(group, "is_active", state == "on")
                where = "home"

            case _:
                log.warning("chat %s: unknown settings action %r", chat_id, data)
                return None

        if patch is not None:
            changed = patch.changes()
            await groups.apply_settings(chat_id, patch)
            reschedule = bool(SCHEDULED_ATTRS & changed.keys())

        members = list(await SubscriberRepo(session).list_active(chat_id))
        return _screen(group, where, len(members), members=members, reschedule=reschedule)


def _origin_of(key: str) -> str:
    """Which editor a stepper belongs to, so the panel stays where it was."""
    return {
        "pages": "pages",
        "page": "page",
        "send": "send",
        "close": "close",
        "qstart": "quiet",
        "qend": "quiet",
        "wtime": "weekly",
    }.get(key, "rem")


# ------------------------------------------------------------------ handlers


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    if not await is_group_admin(update):
        await message.reply_text(ar.ADMIN_ONLY)
        return

    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat.id)
        if group is None:
            await message.reply_text(ar.NOT_REGISTERED)
            return
        text, keyboard = view.home(group, await SubscriberRepo(session).count_active(chat.id))

    await message.reply_text(text, reply_markup=keyboard)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat = update.effective_chat
    if query is None or query.data is None or chat is None:
        return

    # Checked on every press, not only on /settings: the panel sits in the group
    # where anyone can reach its buttons.
    if not await is_group_admin(update):
        await query.answer(ar.ADMIN_ONLY, show_alert=True)
        return

    data = query.data.split(":", 1)[1]
    if data == "done":
        await query.answer(ar.SETTINGS_CLOSED)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except TelegramError:
            log.debug("chat %s: could not clear the panel keyboard", chat.id)
        return

    screen = await apply(get_deps(context), chat.id, data)
    await query.answer()
    if screen is None:
        return

    try:
        await query.edit_message_text(screen.text, reply_markup=screen.keyboard)
    except BadRequest as exc:
        # "not modified" means the value was already at the end of its range.
        if "not modified" not in str(exc).lower():
            log.warning("chat %s: could not redraw the settings panel (%s)", chat.id, exc)

    if screen.reschedule:
        await reschedule_chat(context.application, chat.id)


async def setpage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Jump the group to any page — how a group resumes a khatmah it began without the bot."""
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    if not await is_group_admin(update):
        await message.reply_text(ar.ADMIN_ONLY)
        return

    page = parse_page(context.args[0]) if context.args else None
    if page is None:
        await message.reply_text(ar.SETPAGE_USAGE)
        return

    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        groups = GroupRepo(session)
        if await groups.get(chat.id) is None:
            await message.reply_text(ar.NOT_REGISTERED)
            return
        await groups.set_current_page(chat.id, page)
        info = await MushafRepo(session).page(page)

    await message.reply_text(
        ar.SETPAGE_OK.format(
            page=view.ar_num(page),
            juz=view.ar_num(info.juz) if info else "—",
            surahs=info.surah_names if info else "",
        )
    )
    log.info("chat %s: current page set to %s", chat.id, page)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("settings", settings_command, filters=GROUP_FILTER))
    app.add_handler(CommandHandler("setpage", setpage_command, filters=GROUP_FILTER))
    app.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{view.CB}:"))
