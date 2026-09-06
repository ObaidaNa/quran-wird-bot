"""What to do when Telegram refuses.

`Forbidden` means the bot was kicked, blocked, or the group was deleted — the
one error that retrying can never fix. Without this, a group that removed the
bot would keep receiving a send attempt every morning for as long as the bot
runs, and its jobs would stay scheduled forever.

Deactivating rather than deleting is deliberate: the khatmah progress, the
streaks and the subscriptions all survive, so a group that adds the bot back
resumes from its own page instead of starting over.
"""

from __future__ import annotations

import logging

from telegram.error import Forbidden

from ..db.repo import GroupRepo
from ..db.session import session_scope
from ..deps import Deps

log = logging.getLogger(__name__)


async def deactivate_if_forbidden(deps: Deps, chat_id: int, error: BaseException) -> bool:
    """Deactivate the group if Telegram says the bot is not welcome.

    Returns whether it did. Any other error is left to the caller, since almost
    everything else is worth retrying tomorrow.
    """
    if not isinstance(error, Forbidden):
        return False

    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).set_active(chat_id, False)

    if group is None:
        log.warning("chat %s: forbidden (%s); no such group to deactivate", chat_id, error)
    else:
        log.warning("chat %s: forbidden (%s); group deactivated", chat_id, error)
    return True
