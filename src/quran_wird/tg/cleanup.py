"""Taking the bot's own messages back out of a group.

Only ever used to withdraw a wird that is being replaced: the album, its caption
and the wird message all go, so the group is never left looking at two wirds one
of which no longer answers its buttons.

Telegram lets a bot delete its own messages for 48 hours, which comfortably
covers the same day. Every failure is swallowed — a message that will not go
away is untidy, never a reason to fail the replacement that already happened.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from telegram import Bot
from telegram.error import TelegramError

log = logging.getLogger(__name__)


async def delete_messages(bot: Bot, chat_id: int, message_ids: Iterable[int]) -> int:
    """Delete each message, best effort. Returns how many actually went."""
    deleted = 0
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramError as exc:
            log.info("chat %s: could not delete message %s (%s)", chat_id, message_id, exc)
            continue
        deleted += 1
    return deleted
