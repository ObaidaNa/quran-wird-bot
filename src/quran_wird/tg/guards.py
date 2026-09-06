"""Permission checks for admin-only commands."""

from __future__ import annotations

from telegram import Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}

# Telegram attributes a button press by an anonymous admin to @GroupAnonymousBot
# rather than to a person, and get_member() cannot resolve it. Only admins may
# post anonymously, so the identity itself is the proof.
ANONYMOUS_ADMIN_ID = 1087968824


async def is_group_admin(update: Update) -> bool:
    """Whether the sender may change this group's settings.

    Anonymous admins have no per-user membership to check: a message of theirs
    arrives with sender_chat set to the group, and a button press of theirs
    arrives from @GroupAnonymousBot. Both identities are the check.

    A callback query's effective_message is the panel the *bot* posted, so the
    sender_chat branch cannot be reached by a member pressing a button.
    """
    chat, user = update.effective_chat, update.effective_user
    if chat is None or chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        return False

    message = update.effective_message
    if message is not None and message.sender_chat is not None:
        return message.sender_chat.id == chat.id

    if user is None:
        return False
    if user.id == ANONYMOUS_ADMIN_ID:
        # A button pressed by an anonymous admin; see the constant above.
        return True
    try:
        member = await chat.get_member(user.id)
    except TelegramError:
        return False
    return member.status in ADMIN_STATUSES
