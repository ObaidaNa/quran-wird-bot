"""Page-range and khatmah-advance rules.

Pure functions with no database or Telegram involvement, so the rules that decide
what a group reads next can be tested exhaustively.

`Group.current_page` always means *the first page of the next wird*, never the
last page read. A group that has just finished the mushaf sits at page 1 of the
next khatmah, not at page 604.
"""

from __future__ import annotations

from ..db.models import AdvanceRule
from .schemas import TOTAL_PAGES, PageRange


def next_range(current_page: int, pages_per_day: int) -> PageRange:
    """The page range for the next wird, clamped to the end of the mushaf.

    The clamp matters on the final wird of a khatmah: a group reading 5 pages a
    day that reaches page 602 gets 602-604, not 602-606.
    """
    if pages_per_day < 1:
        raise ValueError("pages_per_day must be at least 1")
    start = max(1, min(current_page, TOTAL_PAGES))
    return PageRange(start=start, end=min(start + pages_per_day - 1, TOTAL_PAGES))


def advance_from(page_end: int) -> tuple[int, bool]:
    """Return (next current_page, khatmah_completed) after finishing `page_end`."""
    if page_end >= TOTAL_PAGES:
        return 1, True
    return page_end + 1, False


def should_advance(rule: AdvanceRule, done_count: int, subscriber_count: int) -> bool:
    """Whether the group's pages move forward, given who finished today.

    With no subscribers at all, only ALWAYS advances — otherwise an empty group
    would silently burn through the whole mushaf.
    """
    match rule:
        case AdvanceRule.ALWAYS:
            return True
        case AdvanceRule.ANYONE:
            return done_count >= 1
        case AdvanceRule.ALL:
            return subscriber_count > 0 and done_count >= subscriber_count
        case AdvanceRule.MAJORITY:
            return subscriber_count > 0 and done_count * 2 > subscriber_count
    raise ValueError(f"unknown advance rule: {rule!r}")


def progress_bar(current_page: int, width: int = 10) -> str:
    """A filled/empty block bar for the weekly report."""
    done = max(0, min(current_page - 1, TOTAL_PAGES))
    filled = round(done / TOTAL_PAGES * width)
    return "▰" * filled + "▱" * (width - filled)
