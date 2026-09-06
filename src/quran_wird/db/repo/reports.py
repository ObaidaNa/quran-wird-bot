"""The weekly report log and the badge ledger.

Both exist to make an announcement happen exactly once. A weekly report row is
claimed before the message is sent, the same way `TaskRepo.log_reminder` guards
a reminder, so a restart on a Friday evening cannot post the board twice.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Badge, BadgeKind, WeeklyReport


class WeeklyReportRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, chat_id: int, week_start: dt.date) -> WeeklyReport | None:
        return await self.session.get(WeeklyReport, (chat_id, week_start))

    async def claim(
        self,
        chat_id: int,
        *,
        week_start: dt.date,
        week_end: dt.date,
        pages_read: int = 0,
        active_days: int = 0,
        perfect_user_ids: list[int] | None = None,
    ) -> WeeklyReport | None:
        """Record this week's report before sending it.

        Returns None if the week was already reported, which is what stops a
        second board going out after a restart.
        """
        if await self.get(chat_id, week_start) is not None:
            return None
        report = WeeklyReport(
            chat_id=chat_id,
            week_start=week_start,
            week_end=week_end,
            pages_read=pages_read,
            active_days=active_days,
            perfect_user_ids=perfect_user_ids or [],
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def set_message(self, chat_id: int, week_start: dt.date, message_id: int) -> None:
        report = await self.get(chat_id, week_start)
        if report is not None:
            report.message_id = message_id

    async def recent(self, chat_id: int, *, limit: int = 8) -> Sequence[WeeklyReport]:
        """Past weeks, newest first — the basis for a pace estimate."""
        result = await self.session.scalars(
            select(WeeklyReport)
            .where(WeeklyReport.chat_id == chat_id)
            .order_by(WeeklyReport.week_start.desc())
            .limit(limit)
        )
        return result.all()


class BadgeRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def award(self, chat_id: int, user_id: int, badge: BadgeKind, *, ref: str = "") -> bool:
        """Grant a badge once. Returns False if it was already held.

        `ref` separates repeat awards of the same badge — a week start date for
        a perfect week, a khatmah number for a khatmah.
        """
        key = (chat_id, user_id, badge, ref)
        if await self.session.get(Badge, key) is not None:
            return False
        self.session.add(Badge(chat_id=chat_id, user_id=user_id, badge=badge, ref=ref))
        await self.session.flush()
        return True

    async def list_for(self, chat_id: int, user_id: int) -> Sequence[Badge]:
        result = await self.session.scalars(
            select(Badge)
            .where(Badge.chat_id == chat_id, Badge.user_id == user_id)
            .order_by(Badge.earned_at)
        )
        return result.all()

    async def count(self, chat_id: int, user_id: int, badge: BadgeKind) -> int:
        rows = await self.session.scalars(
            select(Badge).where(
                Badge.chat_id == chat_id,
                Badge.user_id == user_id,
                Badge.badge == badge,
            )
        )
        return len(rows.all())
