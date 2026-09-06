"""The core provider: the mushaf pages for today's wird.

The header line (juz and surah names) is not built here — it belongs to the wird
itself rather than to any one provider, so the send job reads it from MushafRepo.
"""

from __future__ import annotations

from .base import BuildContext, ContentBlock


class QuranPagesProvider:
    key = "quran_pages"

    async def build(self, ctx: BuildContext) -> ContentBlock:
        return ContentBlock(key=self.key, photos=ctx.pages.pages)


provider = QuranPagesProvider()
