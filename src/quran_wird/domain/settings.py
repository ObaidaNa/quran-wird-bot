"""The settings panel's arithmetic: bounds, steppers, and toggles.

Pure functions and a field registry, with no Telegram and no database, so every
bound and every wrap-around is testable on its own. The Arabic labels for these
fields live in `messages/settings_view.py`; only the numbers live here.

Every change still goes through `GroupSettingsPatch` before it reaches the
database, so the bounds below are the panel's guard rails, not the only ones.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .schemas import TOTAL_PAGES

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass(frozen=True)
class NumericField:
    """A whole-number setting the panel edits with − / + buttons."""

    attr: str
    lo: int
    hi: int


# Keyed by the short name that travels in the callback data.
NUMERIC: dict[str, NumericField] = {
    "pages": NumericField("pages_per_day", 1, 20),
    "rmax": NumericField("reminder_max_count", 0, 6),
    "rfirst": NumericField("first_reminder_after_hours", 1, 23),
    "rstep": NumericField("reminder_interval_hours", 1, 12),
    "mentions": NumericField("mentions_per_message", 1, 10),
    "page": NumericField("current_page", 1, TOTAL_PAGES),
}

# Time settings, edited in minutes. Their bounds are the clock itself.
TIME_ATTRS: dict[str, str] = {
    "send": "send_time",
    "close": "day_close_time",
    "qstart": "quiet_hours_start",
    "qend": "quiet_hours_end",
    "wtime": "weekly_report_time",
}


def bump_int(current: int, delta: int, *, lo: int, hi: int) -> int:
    """Step a number and clamp it, so the ends of the range are quiet walls."""
    return max(lo, min(hi, current + delta))


def bump_time(current: dt.time, minutes: int) -> dt.time:
    """Step a clock time, wrapping around midnight in both directions.

    Wrapping rather than clamping: 00:00 minus half an hour is 23:30, which is a
    perfectly ordinary time to close a day in.
    """
    total = (current.hour * 60 + current.minute + minutes) % (24 * 60)
    return dt.time(hour=total // 60, minute=total % 60)


def toggle_weekday(days: list[int], day: int) -> list[int]:
    """Switch one weekday on or off, keeping the list sorted.

    Turning off the last remaining day is refused: a group with no active day
    would never receive a wird again, and the panel would look broken rather
    than paused. `is_active` is how a group takes a break.
    """
    current = set(days or [])
    if day in current:
        if len(current) == 1:
            return sorted(current)
        current.remove(day)
    else:
        current.add(day)
    return sorted(current)


def parse_page(text: str) -> int | None:
    """Read a page number typed with either Arabic-Indic or Western digits."""
    cleaned = text.strip().translate(_ARABIC_DIGITS)
    if not cleaned.isdigit():
        return None
    page = int(cleaned)
    return page if 1 <= page <= TOTAL_PAGES else None


def days_to_finish(pages_per_day: int, *, pages_left: int = TOTAL_PAGES) -> int:
    """Roughly how many reading days a khatmah takes at this pace."""
    return -(-pages_left // max(1, pages_per_day))
