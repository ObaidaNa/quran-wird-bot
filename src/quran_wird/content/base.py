"""The content provider contract.

A provider turns "group G, on date D, reading pages X-Y" into something to send.
Today there is one provider (the mushaf pages). Tafsir, azkar and hadith are
planned, and each will be a new module implementing this same protocol plus a
line in the group's settings — the send job below does not change.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Group
from ..domain.schemas import PageRange


@dataclass(slots=True)
class BuildContext:
    session: AsyncSession
    group: Group
    task_date: dt.date
    pages: PageRange
    pages_dir: Path


@dataclass(slots=True)
class ContentBlock:
    """What a provider contributes to one day's message.

    `photos` are page numbers to send as an album; `text` is appended to the
    wird message body. A text-only provider (tafsir) leaves `photos` empty.
    """

    key: str
    photos: list[int] = field(default_factory=list)
    text: str = ""
    caption: str | None = None


@runtime_checkable
class ContentProvider(Protocol):
    key: str

    async def build(self, ctx: BuildContext) -> ContentBlock | None: ...
