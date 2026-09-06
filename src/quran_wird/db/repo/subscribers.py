"""Opt-in subscribers, plus the weekly nudge for people who read without joining."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Nudge, Subscriber, utcnow

NUDGE_INTERVAL = dt.timedelta(days=7)


class SubscriberRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, chat_id: int, user_id: int) -> Subscriber | None:
        return await self.session.get(Subscriber, (chat_id, user_id))

    async def is_active(self, chat_id: int, user_id: int) -> bool:
        sub = await self.get(chat_id, user_id)
        return sub is not None and sub.is_active

    async def subscribe(
        self, chat_id: int, user_id: int, *, display_name: str, username: str | None = None
    ) -> tuple[Subscriber, bool]:
        """Subscribe a member. Returns (subscriber, newly_subscribed).

        Someone who left and comes back keeps their original row — and therefore
        their history in user_stats — instead of starting over.
        """
        sub = await self.session.get(Subscriber, (chat_id, user_id))
        if sub is None:
            sub = Subscriber(
                chat_id=chat_id,
                user_id=user_id,
                display_name=display_name,
                username=username,
            )
            self.session.add(sub)
            await self.session.flush()
            return sub, True

        was_active = sub.is_active
        sub.display_name = display_name
        sub.username = username
        sub.is_active = True
        sub.left_at = None
        return sub, not was_active

    async def unsubscribe(self, chat_id: int, user_id: int) -> bool:
        """Deactivate without deleting, so past completions stay attributable."""
        sub = await self.session.get(Subscriber, (chat_id, user_id))
        if sub is None or not sub.is_active:
            return False
        sub.is_active = False
        sub.left_at = utcnow()
        return True

    async def list_active(self, chat_id: int) -> Sequence[Subscriber]:
        result = await self.session.scalars(
            select(Subscriber)
            .where(Subscriber.chat_id == chat_id, Subscriber.is_active.is_(True))
            .order_by(Subscriber.joined_at)
        )
        return result.all()

    async def count_active(self, chat_id: int) -> int:
        return len(await self.list_active(chat_id))

    async def touch_name(
        self, chat_id: int, user_id: int, *, display_name: str, username: str | None = None
    ) -> None:
        """Keep the stored name current; people rename themselves on Telegram."""
        sub = await self.session.get(Subscriber, (chat_id, user_id))
        if sub is not None:
            sub.display_name = display_name
            sub.username = username


class NudgeRepo:
    """Rate-limits the 'you read it, why not subscribe?' invitation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def due(self, chat_id: int, user_id: int, *, now: dt.datetime | None = None) -> bool:
        now = now or utcnow()
        nudge = await self.session.get(Nudge, (chat_id, user_id))
        if nudge is None:
            return True
        return now - nudge.last_nudged_at >= NUDGE_INTERVAL

    async def record(self, chat_id: int, user_id: int, *, now: dt.datetime | None = None) -> None:
        now = now or utcnow()
        nudge = await self.session.get(Nudge, (chat_id, user_id))
        if nudge is None:
            self.session.add(Nudge(chat_id=chat_id, user_id=user_id, last_nudged_at=now))
        else:
            nudge.last_nudged_at = now
