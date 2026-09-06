"""Global error handler."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError

from ..deps import get_deps
from ..messages import ar
from ..tg.failures import deactivate_if_forbidden

log = logging.getLogger(__name__)


async def on_error(update: object, context) -> None:
    error = context.error
    log.exception("unhandled error while processing update", exc_info=error)

    if not isinstance(update, Update):
        return

    chat = update.effective_chat
    if chat is None:
        return

    # Replying to a chat that has blocked or removed the bot is impossible, so
    # the group is deactivated instead of retried forever.
    if error is not None and await deactivate_if_forbidden(get_deps(context), chat.id, error):
        return

    message = update.effective_message
    if message is None:
        return
    try:
        await message.reply_text(ar.GENERIC_ERROR)
    except TelegramError:
        log.debug("could not deliver the error notice to chat %s", chat.id)
