"""Bot lifecycle in a group: being added, /start, /help, and members leaving."""

from __future__ import annotations

import logging

from telegram import Chat, ChatMemberUpdated, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, ChatMemberHandler, CommandHandler, ContextTypes

from ..db.repo import GroupRepo, SubscriberRepo
from ..db.session import session_scope
from ..deps import get_deps
from ..jobs.scheduler import reschedule_chat
from ..messages import ar

log = logging.getLogger(__name__)

GROUP_TYPES = (Chat.GROUP, Chat.SUPERGROUP)
PRESENT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


def _became_present(update: ChatMemberUpdated) -> bool | None:
    """True when the subject joined, False when it left, None when nothing changed.

    Telegram sends a my_chat_member update for promotions and title changes too,
    so a plain "was it in the chat before / is it now" comparison is what
    distinguishes a real join from noise.
    """
    old, new = update.difference().get("status", (None, None))
    if old is None and new is None:
        return None
    was, now = old in PRESENT, new in PRESENT
    return None if was == now else now


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The bot itself was added to or removed from a chat."""
    member_update = update.my_chat_member
    chat = update.effective_chat
    if member_update is None or chat is None or chat.type not in GROUP_TYPES:
        return

    present = _became_present(member_update)
    if present is None:
        return

    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        groups = GroupRepo(session)
        if not present:
            # Removed from the group: stop all activity but keep the khatmah
            # progress, so re-adding the bot resumes instead of restarting.
            await groups.set_active(chat.id, False)
            log.info("removed from chat %s; group deactivated", chat.id)
            # Drops the group's jobs too; otherwise they keep firing into a
            # chat the bot is no longer in.
            await reschedule_chat(context.application, chat.id)
            return

        _, created = await groups.get_or_create(
            chat.id, title=chat.title, timezone=deps.settings.default_timezone
        )
        await groups.set_active(chat.id, True)

    # Jobs live in memory and are otherwise only built at startup, so without
    # this a group that adds the bot receives nothing until the next restart.
    await reschedule_chat(context.application, chat.id)

    await context.bot.send_message(chat.id, ar.ADDED_TO_GROUP)
    log.info("added to chat %s (%s), new=%s", chat.id, chat.title, created)


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A member left or was removed: deactivate their subscription."""
    member_update = update.chat_member
    chat = update.effective_chat
    if member_update is None or chat is None:
        return
    if _became_present(member_update) is not False:
        return

    user = member_update.new_chat_member.user
    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        if await SubscriberRepo(session).unsubscribe(chat.id, user.id):
            log.info("member %s left chat %s; subscription deactivated", user.id, chat.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return

    if chat.type not in GROUP_TYPES:
        await message.reply_text(ar.START_PRIVATE)
        return

    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        groups = GroupRepo(session)
        group, created = await groups.get_or_create(
            chat.id, title=chat.title, timezone=deps.settings.default_timezone
        )
        await groups.set_active(chat.id, True)
        count = await SubscriberRepo(session).count_active(chat.id)
        page, khatmah = group.current_page, group.khatmah_number

    # /start is the other way a group comes into being — and the way a group
    # that was paused or re-added gets its jobs back.
    await reschedule_chat(context.application, chat.id)

    if created:
        await message.reply_text(ar.START_GROUP_NEW)
    else:
        await message.reply_text(
            ar.START_GROUP_AGAIN.format(count=count, page=page, khatmah=khatmah)
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(ar.HELP)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
