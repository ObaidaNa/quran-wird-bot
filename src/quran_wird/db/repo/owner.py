"""Fleet-wide aggregates for the owner panel.

Every other repo answers about one group; this one answers about all of them at
once — how many groups exist, how many people they hold, how far the khatmahs
have travelled between them. Nothing here is ever shown inside a group.

The counts are read in one pass rather than by walking the groups, because the
panel is opened on a whim and the database is the only copy of any of it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.schemas import TOTAL_PAGES, GroupDigest
from ..models import Completion, DailyTask, Group, Subscriber, UserStats


class OwnerStatsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _count(self, stmt) -> int:
        return await self.session.scalar(stmt) or 0

    # ------------------------------------------------------------------ groups

    async def group_counts(self) -> tuple[int, int]:
        """(every group ever registered, those still active).

        A group is kept after the bot is removed so re-adding resumes its
        khatmah, so the difference is the groups that left or paused.
        """
        total = await self._count(select(func.count()).select_from(Group))
        active = await self._count(
            select(func.count()).select_from(Group).where(Group.is_active.is_(True))
        )
        return total, active

    async def groups_created_since(self, since: dt.datetime) -> int:
        return await self._count(
            select(func.count()).select_from(Group).where(Group.created_at >= since)
        )

    # ----------------------------------------------------------------- members

    async def unique_users(self) -> int:
        """People, not subscriptions — one person in three groups counts once."""
        return await self._count(select(func.count(func.distinct(Subscriber.user_id))))

    async def subscription_counts(self) -> tuple[int, int]:
        """(every subscription ever made, those still active)."""
        total = await self._count(select(func.count()).select_from(Subscriber))
        active = await self._count(
            select(func.count()).select_from(Subscriber).where(Subscriber.is_active.is_(True))
        )
        return total, active

    async def streaks(self) -> tuple[int, int]:
        """(how many members have a live streak, the longest one running)."""
        on_streak = await self._count(
            select(func.count()).select_from(UserStats).where(UserStats.current_streak > 0)
        )
        longest = await self._count(select(func.max(UserStats.current_streak)))
        return on_streak, longest

    async def reading_totals(self) -> tuple[int, int]:
        """(days completed, days missed) summed across every member of every group."""
        done = await self._count(select(func.sum(UserStats.total_done)))
        missed = await self._count(select(func.sum(UserStats.total_missed)))
        return done, missed

    # ---------------------------------------------------------------- khatmahs

    async def khatmah_totals(self) -> tuple[int, int]:
        """(khatmahs finished, pages read across all of them).

        A group on khatmah 3 has finished two. Pages are counted the same way —
        the completed khatmahs in full, plus how far into the current one the
        group has read.
        """
        finished = await self._count(select(func.sum(Group.khatmah_number - 1)))
        pages = await self._count(
            select(func.sum((Group.khatmah_number - 1) * TOTAL_PAGES + Group.current_page - 1))
        )
        return finished, pages

    # ---------------------------------------------------------------- activity

    async def wird_counts(self, *, today: dt.date, week_start: dt.date) -> tuple[int, int, int]:
        """(wirds ever sent, sent today, sent in the last seven days).

        `task_date` is local to each group, so a fleet spread across timezones
        makes "today" approximate. For a count of this kind that is close enough.
        """
        total = await self._count(select(func.count()).select_from(DailyTask))
        on_day = await self._count(
            select(func.count()).select_from(DailyTask).where(DailyTask.task_date == today)
        )
        week = await self._count(
            select(func.count()).select_from(DailyTask).where(DailyTask.task_date >= week_start)
        )
        return total, on_day, week

    async def completion_counts(
        self, since: dt.datetime, week_since: dt.datetime
    ) -> tuple[int, int, int]:
        """(ticks ever recorded, since `since`, since `week_since`)."""
        total = await self._count(select(func.count()).select_from(Completion))
        recent = await self._count(
            select(func.count()).select_from(Completion).where(Completion.done_at >= since)
        )
        week = await self._count(
            select(func.count()).select_from(Completion).where(Completion.done_at >= week_since)
        )
        return total, recent, week

    async def groups_reading_since(self, week_start: dt.date) -> int:
        """Groups that received a wird in the window — the fleet that is alive."""
        return await self._count(
            select(func.count(func.distinct(DailyTask.chat_id))).where(
                DailyTask.task_date >= week_start
            )
        )

    # ----------------------------------------------------------------- listing

    async def digests(self, *, limit: int = 10, by_progress: bool = False) -> Sequence[GroupDigest]:
        """The groups themselves, biggest first — or nearest the khatmah's end.

        The subscriber count is joined rather than counted per group, so listing
        the fleet stays one query however many groups there are.
        """
        subscribers = (
            select(Subscriber.chat_id, func.count().label("members"))
            .where(Subscriber.is_active.is_(True))
            .group_by(Subscriber.chat_id)
            .subquery()
        )
        last_wird = (
            select(DailyTask.chat_id, func.max(DailyTask.task_date).label("last_date"))
            .group_by(DailyTask.chat_id)
            .subquery()
        )
        members = func.coalesce(subscribers.c.members, 0)

        stmt = (
            select(Group, members, last_wird.c.last_date)
            .outerjoin(subscribers, subscribers.c.chat_id == Group.chat_id)
            .outerjoin(last_wird, last_wird.c.chat_id == Group.chat_id)
        )
        if by_progress:
            # How near the end of the *current* khatmah, which is what the page
            # number measures. Ordering by khatmah first would rank a group on
            # its fourth khatmah at page 200 above one at page 590 on its first,
            # and the heading promises the opposite. A paused group is left out
            # because it is not going to finish anything.
            stmt = stmt.where(Group.is_active.is_(True)).order_by(Group.current_page.desc())
        else:
            stmt = stmt.order_by(members.desc(), Group.created_at.desc())

        rows = await self.session.execute(stmt.limit(limit))
        return [
            GroupDigest(
                chat_id=group.chat_id,
                title=group.title,
                is_active=group.is_active,
                subscribers=count,
                current_page=group.current_page,
                khatmah_number=group.khatmah_number,
                last_wird=last_date,
            )
            for group, count, last_date in rows
        ]
