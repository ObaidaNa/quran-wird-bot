"""Group settings and khatmah progress."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.schemas import GroupSettingsPatch
from ..models import Group


class GroupRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, chat_id: int) -> Group | None:
        return await self.session.get(Group, chat_id)

    async def get_or_create(
        self, chat_id: int, *, title: str | None = None, timezone: str = "Asia/Damascus"
    ) -> tuple[Group, bool]:
        """Fetch the group, creating it on first contact. Returns (group, created)."""
        group = await self.session.get(Group, chat_id)
        if group is not None:
            # Re-adding the bot to a group it already knows reactivates it rather
            # than wiping the khatmah progress.
            if title and group.title != title:
                group.title = title
            return group, False

        group = Group(
            chat_id=chat_id,
            title=title,
            timezone=timezone,
            khatmah_started_on=dt.date.today(),
        )
        self.session.add(group)
        await self.session.flush()
        return group, True

    async def list_active(self) -> Sequence[Group]:
        """Every group the scheduler should build jobs for."""
        result = await self.session.scalars(select(Group).where(Group.is_active.is_(True)))
        return result.all()

    async def set_active(self, chat_id: int, active: bool) -> Group | None:
        group = await self.session.get(Group, chat_id)
        if group is not None:
            group.is_active = active
        return group

    async def apply_settings(self, chat_id: int, patch: GroupSettingsPatch) -> Group | None:
        """Apply a validated settings patch. Unset fields are left untouched."""
        group = await self.session.get(Group, chat_id)
        if group is None:
            return None
        for field, value in patch.changes().items():
            setattr(group, field, value)
        return group

    async def set_current_page(self, chat_id: int, page: int) -> Group | None:
        group = await self.session.get(Group, chat_id)
        if group is not None:
            group.current_page = page
        return group

    async def start_new_khatmah(self, chat_id: int, *, on: dt.date | None = None) -> Group | None:
        """Roll over to the next khatmah, back to page 1."""
        group = await self.session.get(Group, chat_id)
        if group is not None:
            group.khatmah_number += 1
            group.current_page = 1
            group.khatmah_started_on = on or dt.date.today()
        return group
