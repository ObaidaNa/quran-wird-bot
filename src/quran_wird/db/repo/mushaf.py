"""Read-only access to the mushaf reference index."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PageIndex


class MushafRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, page_no: int) -> PageIndex | None:
        return await self.session.get(PageIndex, page_no)

    async def range_summary(self, start: int, end: int) -> tuple[int, str]:
        """The juz and the surah names covered by a page range.

        The juz of the first page is used, since that is the juz the wird starts
        in. Surah names are collected across every page in the range and
        de-duplicated in reading order, so a wird that crosses a surah boundary
        names both.
        """
        result = await self.session.scalars(
            select(PageIndex)
            .where(PageIndex.page_no >= start, PageIndex.page_no <= end)
            .order_by(PageIndex.page_no)
        )
        pages = list(result.all())
        if not pages:
            return 1, ""

        names: list[str] = []
        for page in pages:
            for name in page.surah_names.split("، "):
                if name and name not in names:
                    names.append(name)
        return pages[0].juz, "، ".join(names)

    async def page_count(self) -> int:
        return await self.session.scalar(select(func.count()).select_from(PageIndex)) or 0
