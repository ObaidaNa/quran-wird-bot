"""file_id cache for the page images.

Telegram returns a file_id after the first upload of a photo; sending that id
afterwards skips the upload entirely. The ids are bot-specific, so the cache is
global to this bot rather than per group.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PageMedia


class MediaRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_file_id(self, page_no: int) -> str | None:
        media = await self.session.get(PageMedia, page_no)
        return media.file_id if media else None

    async def get_many(self, pages: list[int]) -> dict[int, str]:
        if not pages:
            return {}
        result = await self.session.scalars(select(PageMedia).where(PageMedia.page_no.in_(pages)))
        return {m.page_no: m.file_id for m in result.all()}

    async def remember(
        self, page_no: int, file_id: str, *, file_unique_id: str | None = None
    ) -> None:
        media = await self.session.get(PageMedia, page_no)
        if media is None:
            self.session.add(
                PageMedia(page_no=page_no, file_id=file_id, file_unique_id=file_unique_id)
            )
        else:
            media.file_id = file_id
            media.file_unique_id = file_unique_id

    async def forget(self, page_no: int) -> None:
        """Drop a cached id — used when Telegram rejects it as stale."""
        media = await self.session.get(PageMedia, page_no)
        if media is not None:
            await self.session.delete(media)
            # Flush so a later get() in the same session sees it gone.
            await self.session.flush()
