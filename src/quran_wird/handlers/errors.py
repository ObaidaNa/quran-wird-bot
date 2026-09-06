"""Global error handler."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import Forbidden, TelegramError

from ..db.repo import GroupRepo
from ..db.session import session_scope
from ..deps import get_deps
from ..messages import ar

log = logging.getLogger(__name__)


async def on_error(update: object, context) -> None:
    error = context.error
    log.exception("unhandled error while processing update", exc_info=error)

    if not isinstance(update, Update):
        return

    chat = update.effective_chat
    if chat is None:
        return

    # Forbidden means the bot was blocked, kicked, or the group was deleted.
    # Replying is impossible, so deactivate the group instead of retrying forever.
    if isinstance(error, Forbidden):
        deps = get_deps(context)
        async with session_scope(deps.sessions) as session:
            await GroupRepo(session).set_active(chat.id, False)
        log.warning("forbidden in chat %s; group deactivated", chat.id)
        return

    message = update.effective_message
    if message is None:
        return
    try:
        await message.reply_text(ar.GENERIC_ERROR)
    except TelegramError:
        log.debug("could not deliver the error notice to chat %s", chat.id)
