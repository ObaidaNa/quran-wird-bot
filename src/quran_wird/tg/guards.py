"""Permission checks for admin-only commands."""

from __future__ import annotations

from telegram import Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


async def is_group_admin(update: Update) -> bool:
    """Whether the sender may change this group's settings.

    Anonymous admins post as the group itself, and in that case Telegram gives no
    per-user membership to check, so the sender_chat identity is what we trust.
    """
    chat, user = update.effective_chat, update.effective_user
    if chat is None or chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        return False

    message = update.effective_message
    if message is not None and message.sender_chat is not None:
        return message.sender_chat.id == chat.id

    if user is None:
        return False
    try:
        member = await chat.get_member(user.id)
    except TelegramError:
        return False
    return member.status in ADMIN_STATUSES
