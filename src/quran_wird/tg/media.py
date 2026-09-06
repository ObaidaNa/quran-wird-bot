"""Sending mushaf page images, with a file_id cache.

The first send of a page uploads ~180 KB; Telegram answers with a file_id that
can be sent instead of the bytes forever after. With 604 pages and many groups
that is the difference between re-uploading the mushaf daily and sending a few
short strings.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import Bot, InputMediaPhoto, Message
from telegram.error import BadRequest

from ..db.repo import MediaRepo

log = logging.getLogger(__name__)

# Telegram accepts 2-10 items in one media group; a single image must go through
# send_photo instead, and a longer wird is split across several albums.
MAX_ALBUM = 10


class PageImageMissing(FileNotFoundError):
    """A page image is not on disk — scripts/build_pages.py has not been run."""


def page_path(pages_dir: Path, page_no: int) -> Path:
    path = pages_dir / f"{page_no:03d}.png"
    if not path.exists():
        raise PageImageMissing(f"page image not found: {path} - run: uv run scripts/build_pages.py")
    return path


def _largest_file_id(message: Message) -> str | None:
    """Telegram returns several sizes; the last is the largest."""
    return message.photo[-1].file_id if message.photo else None


async def _remember(media: MediaRepo, pages: list[int], messages: tuple[Message, ...]) -> None:
    for page_no, message in zip(pages, messages, strict=False):
        file_id = _largest_file_id(message)
        if file_id:
            await media.remember(page_no, file_id)


def _source(cached: dict[int, str], pages_dir: Path, page_no: int) -> tuple[str | bytes, str]:
    """Either a cached file_id, or the image bytes plus a filename.

    Bytes rather than a Path on purpose: InputMediaPhoto parses a Path with
    local_mode hardcoded to True, turning it into a file:// URI that the public
    Bot API cannot fetch. Bytes always become a real upload.
    """
    file_id = cached.get(page_no)
    filename = f"{page_no:03d}.png"
    if file_id:
        return file_id, filename
    return page_path(pages_dir, page_no).read_bytes(), filename


async def _send_chunk(
    bot: Bot,
    chat_id: int,
    pages: list[int],
    pages_dir: Path,
    media: MediaRepo,
    caption: str | None,
    *,
    use_cache: bool,
) -> tuple[Message, ...]:
    cached = await media.get_many(pages) if use_cache else {}

    if len(pages) == 1:
        source, filename = _source(cached, pages_dir, pages[0])
        message = await bot.send_photo(chat_id, photo=source, caption=caption, filename=filename)
        return (message,)

    items = []
    for index, page_no in enumerate(pages):
        source, filename = _source(cached, pages_dir, page_no)
        items.append(
            # Only the first item's caption is shown for an album.
            InputMediaPhoto(
                media=source,
                caption=caption if index == 0 else None,
                filename=filename,
            )
        )
    return await bot.send_media_group(chat_id, media=items)


async def send_pages(
    bot: Bot,
    chat_id: int,
    pages: list[int],
    *,
    pages_dir: Path,
    media: MediaRepo,
    caption: str | None = None,
) -> list[int]:
    """Send the given pages as photos. Returns the sent message ids.

    A cached file_id can go stale (Telegram expires them rarely, but it happens).
    Rather than failing the whole wird, the chunk is retried once with the bytes
    from disk and the bad ids are dropped from the cache.
    """
    if not pages:
        return []

    message_ids: list[int] = []
    for start in range(0, len(pages), MAX_ALBUM):
        chunk = pages[start : start + MAX_ALBUM]
        chunk_caption = caption if start == 0 else None

        try:
            messages = await _send_chunk(
                bot, chat_id, chunk, pages_dir, media, chunk_caption, use_cache=True
            )
        except BadRequest as exc:
            log.warning("send failed for pages %s (%s); retrying from disk", chunk, exc)
            for page_no in chunk:
                await media.forget(page_no)
            messages = await _send_chunk(
                bot, chat_id, chunk, pages_dir, media, chunk_caption, use_cache=False
            )

        await _remember(media, chunk, messages)
        message_ids.extend(m.message_id for m in messages)

    return message_ids
