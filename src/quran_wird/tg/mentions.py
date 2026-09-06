"""Names and mentions rendered for HTML parse mode."""

from __future__ import annotations

import html
from collections.abc import Iterable, Iterator
from typing import Protocol

MAX_NAME_LEN = 64


class NamedUser(Protocol):
    user_id: int
    display_name: str


def safe_name(name: str | None, *, fallback: str = "أخي الكريم") -> str:
    """Escape a display name for HTML and keep it short enough to read inline."""
    cleaned = (name or "").strip()
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > MAX_NAME_LEN:
        cleaned = cleaned[: MAX_NAME_LEN - 1] + "…"
    return html.escape(cleaned)


def mention(user_id: int, name: str | None) -> str:
    """A tappable mention that works even without a @username.

    Members are only mentioned after they have interacted with the bot, so the
    tg:// form always resolves for them.
    """
    return f'<a href="tg://user?id={user_id}">{safe_name(name)}</a>'


def chunked(items: Iterable[NamedUser], size: int) -> Iterator[list[NamedUser]]:
    """Split members into batches, so one reminder never tags a whole crowd."""
    if size < 1:
        raise ValueError("size must be at least 1")
    batch: list[NamedUser] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
