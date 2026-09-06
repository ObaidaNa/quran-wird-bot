"""Opting in and out of the daily wird: /join and /leave."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from ..db.repo import GroupRepo, SubscriberRepo
from ..db.session import session_scope
from ..deps import get_deps
from ..messages import ar
from ..tg.mentions import safe_name

log = logging.getLogger(__name__)

# Arabic aliases so members can type what comes naturally.
JOIN_COMMANDS = ["join", "subscribe", "quran", "wird"]
LEAVE_COMMANDS = ["leave", "unsubscribe"]

GROUP_FILTER = filters.ChatType.GROUPS


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or message is None:
        return

    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat.id)
        if group is None:
            await message.reply_text(ar.NOT_REGISTERED)
            return

        subs = SubscriberRepo(session)
        existing = await subs.get(chat.id, user.id)
        was_active = existing is not None and existing.is_active

        _, changed = await subs.subscribe(
            chat.id,
            user.id,
            display_name=user.full_name,
            username=user.username,
        )
        count = await subs.count_active(chat.id)

    name = safe_name(user.full_name)
    if was_active:
        text = ar.JOIN_ALREADY.format(name=name)
    elif existing is not None:
        text = ar.JOIN_BACK.format(name=name, count=count)
    else:
        text = ar.JOIN_OK.format(name=name, count=count)

    await message.reply_text(text)
    if changed:
        log.info("user %s joined chat %s (total %s)", user.id, chat.id, count)


async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or message is None:
        return

    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        removed = await SubscriberRepo(session).unsubscribe(chat.id, user.id)

    name = safe_name(user.full_name)
    await message.reply_text(
        ar.LEAVE_OK.format(name=name) if removed else ar.LEAVE_NOT_SUBSCRIBED.format(name=name)
    )
    if removed:
        log.info("user %s left chat %s", user.id, chat.id)


async def group_only_notice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer /join and /leave sent in a private chat instead of ignoring them."""
    message = update.effective_message
    if message is not None:
        await message.reply_text(ar.GROUP_ONLY)


def register(app: Application) -> None:
    commands = JOIN_COMMANDS + LEAVE_COMMANDS
    app.add_handler(CommandHandler(JOIN_COMMANDS, join, filters=GROUP_FILTER))
    app.add_handler(CommandHandler(LEAVE_COMMANDS, leave, filters=GROUP_FILTER))
    app.add_handler(CommandHandler(commands, group_only_notice, filters=filters.ChatType.PRIVATE))
