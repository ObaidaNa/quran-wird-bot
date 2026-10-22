"""Catch-up plans and their event log."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatchupEvent, CatchupPlan, CatchupState, Group, Subscriber, utcnow


class CatchupEventKind:
    """String values for CatchupEvent.kind — kept as plain constants, not an
    enum, because they are written once and read only for reporting."""

    ENROLLED = "enrolled"
    PIECE_SENT = "piece_sent"
    PIECE_DONE = "piece_done"
    NUDGE = "nudge"
    PAUSED = "paused"
    DORMANT = "dormant"
    RESUMED = "resumed"
    REPAIRED_DAY = "repaired_day"
    CAUGHT_UP = "caught_up"


class CatchupRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, chat_id: int, user_id: int) -> CatchupPlan | None:
        return await self.session.get(CatchupPlan, (chat_id, user_id))

    async def list_for_user(self, user_id: int) -> Sequence[CatchupPlan]:
        """All plans still linked to an active group subscription."""
        result = await self.session.scalars(
            select(CatchupPlan)
            .join(Group, Group.chat_id == CatchupPlan.chat_id)
            .join(
                Subscriber,
                (
                    (Subscriber.chat_id == CatchupPlan.chat_id)
                    & (Subscriber.user_id == CatchupPlan.user_id)
                ),
            )
            .where(
                CatchupPlan.user_id == user_id,
                Group.is_active.is_(True),
                Subscriber.is_active.is_(True),
            )
            .order_by(CatchupPlan.enrolled_at)
        )
        return result.all()

    async def create(self, chat_id: int, user_id: int, **fields) -> CatchupPlan:
        plan = CatchupPlan(chat_id=chat_id, user_id=user_id, **fields)
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def list_active(self) -> Sequence[CatchupPlan]:
        """Plans the tick should look at: active, or paused and owed a probe.

        Dormant and blocked plans are deliberately excluded — those members
        asked for silence, by ignoring the bot or by blocking it, and only
        their own hand breaks it.
        """
        result = await self.session.scalars(
            select(CatchupPlan).where(
                CatchupPlan.state.in_([CatchupState.ACTIVE, CatchupState.PAUSED])
            )
        )
        return result.all()

    async def log(
        self,
        chat_id: int,
        user_id: int,
        kind: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
        at: dt.datetime | None = None,
    ) -> CatchupEvent:
        event = CatchupEvent(
            chat_id=chat_id,
            user_id=user_id,
            kind=kind,
            page_start=page_start,
            page_end=page_end,
            at=at or utcnow(),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def weekly_progress(
        self, chat_id: int, start: dt.datetime, end: dt.datetime
    ) -> dict[int, set[int]]:
        """Pages each member caught up in the window: user_id → page numbers.

        Use a set so an explicit resend or repeated range cannot inflate the
        weekly page count.
        """
        result = await self.session.scalars(
            select(CatchupEvent).where(
                CatchupEvent.chat_id == chat_id,
                CatchupEvent.kind == CatchupEventKind.PIECE_DONE,
                CatchupEvent.at >= start,
                CatchupEvent.at <= end,
            )
        )
        pages: dict[int, set[int]] = {}
        for event in result:
            if event.page_start is None or event.page_end is None:
                continue
            pages.setdefault(event.user_id, set()).update(
                range(event.page_start, event.page_end + 1)
            )
        return pages
