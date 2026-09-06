"""Per-member streaks and totals."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import UserStats


class StatsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, chat_id: int, user_id: int) -> UserStats:
        stats = await self.session.get(UserStats, (chat_id, user_id))
        if stats is None:
            stats = UserStats(chat_id=chat_id, user_id=user_id)
            self.session.add(stats)
            await self.session.flush()
        return stats

    async def get(self, chat_id: int, user_id: int) -> UserStats | None:
        return await self.session.get(UserStats, (chat_id, user_id))

    async def record_done(self, chat_id: int, user_id: int, on: dt.date) -> UserStats:
        """Count one completed day.

        Idempotent per date: closing the same day twice, or a member pressing the
        button after the day already counted, must not inflate the streak.
        """
        stats = await self.get_or_create(chat_id, user_id)
        if stats.last_done_date == on:
            return stats

        stats.total_done += 1
        stats.current_streak += 1
        stats.best_streak = max(stats.best_streak, stats.current_streak)
        stats.consecutive_missed = 0
        stats.last_done_date = on
        return stats

    async def record_missed(self, chat_id: int, user_id: int) -> UserStats:
        stats = await self.get_or_create(chat_id, user_id)
        stats.total_missed += 1
        stats.consecutive_missed += 1
        stats.current_streak = 0
        return stats

    async def leaderboard(
        self, chat_id: int, *, limit: int = 10, user_ids: Sequence[int] | None = None
    ) -> Sequence[UserStats]:
        """The standings, best current streak first.

        `user_ids` narrows the query rather than the result: filtering after the
        limit would let one departed member with a long streak empty the board.
        """
        stmt = select(UserStats).where(UserStats.chat_id == chat_id)
        if user_ids is not None:
            if not user_ids:
                return []
            stmt = stmt.where(UserStats.user_id.in_(user_ids))
        result = await self.session.scalars(
            stmt.order_by(desc(UserStats.current_streak), desc(UserStats.total_done)).limit(limit)
        )
        return result.all()

    async def top_streak(self, chat_id: int) -> UserStats | None:
        return await self.session.scalar(
            select(UserStats)
            .where(UserStats.chat_id == chat_id, UserStats.current_streak > 0)
            .order_by(desc(UserStats.current_streak))
            .limit(1)
        )
