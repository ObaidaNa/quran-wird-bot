"""Pinning the daily wird, and letting it go when the day ends.

A pinned wird sits at the top of the group all day, which is the point: members
who open the group at midnight still find it without scrolling. It is unpinned
when the day closes, so a group's pin list does not fill up with old wirds.

Pinning needs admin rights the bot may not have been given, and failing to pin
is never a reason to fail a send — the wird itself has already gone out. So
every failure here is logged and swallowed.
"""

from __future__ import annotations

import logging

from telegram import Bot
from telegram.error import TelegramError

log = logging.getLogger(__name__)


async def pin(bot: Bot, chat_id: int, message_id: int) -> bool:
    """Pin the wird quietly. Returns whether it worked."""
    try:
        # The wird message already notified the group a moment ago; a second
        # notification for the pin would be noise.
        await bot.pin_chat_message(chat_id, message_id, disable_notification=True)
    except TelegramError as exc:
        # Almost always "not enough rights": the bot was added without being
        # made an admin. Worth a line in the log, not an alarm.
        log.info("chat %s: could not pin the wird (%s)", chat_id, exc)
        return False
    return True


async def unpin(bot: Bot, chat_id: int, message_id: int) -> bool:
    """Release yesterday's wird. Returns whether it worked."""
    try:
        await bot.unpin_chat_message(chat_id, message_id=message_id)
    except TelegramError as exc:
        log.info("chat %s: could not unpin the wird (%s)", chat_id, exc)
        return False
    return True
