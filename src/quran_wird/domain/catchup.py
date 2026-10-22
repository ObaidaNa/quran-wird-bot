"""The catch-up plan's arithmetic: debt queues, pieces, and the nudge policy.

Pure functions with no database or Telegram involvement. See docs/PLAN.md
section 13 for the product rules these numbers implement.

A plan's debt and pieces are whole-page ranges. Quran content is delivered
only as the existing mushaf photos, never as reconstructed verse text.

The adaptive loop, in one paragraph: a member who reads gets a slightly bigger
piece next time (up to three pages), a member who is nudged and does not
read gets a slightly smaller one (down to one page), nudges come at
widening gaps and rotate tone, seven unanswered nudges pause the plan, a
paused plan is probed once a week, and four unanswered probes make it dormant
— it then waits for the member to come back on their own rather than knocking
forever. Any button press or /start from the member revives it.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Sequence

from ..db.models import CatchupPlan, CatchupState

# ------------------------------------------------------------------ tuning
# Whole mushaf pages per piece. Three successes are needed before increasing
# the dose: one quick click is not evidence that a larger habit will stick.
MIN_DOSE = 1
START_DOSE = 1
MAX_DOSE = 3
DOSE_STEP = 1
SUCCESSES_TO_GROW = 3

# Hours between nudges, by how many have already gone unanswered. The widening
# gap is the anti-spam half of the policy: the less a member responds, the
# less often the bot knocks.
NUDGE_GAPS_HOURS = (3, 6, 12, 24, 24, 48)

# Seven unanswered nudges (roughly four days) pause the plan; a paused plan
# gets one probe a week; four unanswered probes make it dormant.
AUTO_PAUSE_AFTER = 7
PROBE_INTERVAL = dt.timedelta(days=7)
DORMANT_AFTER_PROBES = 4

# When a member sends their first piece of the day without a learned pattern.
DEFAULT_TARGET_HOUR = 9
# How many recent completion hours the hour estimate looks at.
DONE_HOURS_KEPT = 10


# ------------------------------------------------------------- page ranges


def merge_ranges(ranges: Sequence[Sequence[int]]) -> list[list[int]]:
    """Sort and merge overlapping or adjacent [start, end] page ranges.

    A repeated wird makes the same pages appear in two missed tasks; merging is
    what keeps a member from owing page 5 twice.
    """
    merged: list[list[int]] = []
    for start, end in sorted((int(r[0]), int(r[1])) for r in ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def drop_pages(queue: Sequence[Sequence[int]], start: int, end: int) -> list[list[int]]:
    """Remove a page range from the debt — the member read those pages.

    Used when a member finishes a group wird whose pages were still in their
    queue (a repeated wird, or the group lapping them in a new khatmah).
    """
    remaining: list[list[int]] = []
    for q_start, q_end in queue:
        if end < q_start or start > q_end:
            remaining.append([q_start, q_end])
            continue
        if q_start < start:
            remaining.append([q_start, start - 1])
        if q_end > end:
            remaining.append([end + 1, q_end])
    return remaining


def count_pages(queue: Sequence[Sequence[int]]) -> int:
    return sum(end - start + 1 for start, end in queue)


def next_piece(spans: Sequence[tuple[int, int]], cursor: int, dose: int) -> tuple[int, int] | None:
    """Up to `dose` whole pages, without jumping across an unread gap."""
    for span_start, span_end in spans:
        if cursor > span_end:
            continue
        start = max(cursor, span_start)
        return start, min(start + dose - 1, span_end)
    return None


def advance_cursor(
    spans: Sequence[tuple[int, int]], done_end: int
) -> tuple[list[tuple[int, int]], int]:
    """Move past a finished piece: new spans and the new cursor.

    Spans the piece completed drop out entirely; a span the piece reached into
    keeps only its unread tail.
    """
    remaining = [(s, e) for s, e in spans if e > done_end]
    remaining = [(max(s, done_end + 1), e) for s, e in remaining]
    return remaining, done_end + 1


# ------------------------------------------------------------ the adaptive loop


def dose_after_success(dose: int) -> int:
    return min(MAX_DOSE, dose + DOSE_STEP)


def dose_after_lapse(dose: int) -> int:
    """A nudge went unanswered: the piece was probably too ambitious."""
    return max(MIN_DOSE, dose - DOSE_STEP)


def nudge_gap(ignored_streak: int) -> dt.timedelta:
    """How long to wait after nudge number `ignored_streak` before the next."""
    index = min(max(ignored_streak, 0), len(NUDGE_GAPS_HOURS) - 1)
    return dt.timedelta(hours=NUDGE_GAPS_HOURS[index])


def should_auto_pause(ignored_streak: int) -> bool:
    return ignored_streak >= AUTO_PAUSE_AFTER


def probe_due(last_probe_at: dt.datetime | None, now: dt.datetime) -> bool:
    if last_probe_at is None:
        return True
    return now - last_probe_at >= PROBE_INTERVAL


def should_go_dormant(probe_count: int) -> bool:
    return probe_count >= DORMANT_AFTER_PROBES


def best_hour(done_hours: Sequence[int], *, default: int = DEFAULT_TARGET_HOUR) -> int:
    """The local hour the member most often reads at, or the default.

    The plan's first nudge of a day aims for this hour; someone who always
    reads after taraweeh should not be nudged at 9 in the morning.
    """
    if not done_hours:
        return default
    # On equal counts prefer the most recent response hour, deterministically.
    return max(reversed(done_hours), key=done_hours.count)


def remember_hour(done_hours: Sequence[int], hour: int) -> list[int]:
    return [*done_hours, hour][-DONE_HOURS_KEPT:]


# ------------------------------------------------------------ the daily tick

# After a member finishes a piece, the next one is pushed after this gap — but
# the «المقطع التالي الآن» button always pulls it sooner. The gap is what makes
# a day feel like several small appointments rather than one pile.
NEXT_PIECE_GAP = dt.timedelta(minutes=75)


class TickAction(enum.StrEnum):
    """What the tick should do with one plan."""

    SKIP = "skip"
    PIECE = "piece"  # push the next piece
    NUDGE = "nudge"  # the pending piece went unanswered too long
    PAUSE_NOTICE = "pause_notice"  # tell the member the plan paused itself
    PROBE = "probe"  # the weekly knock on a paused plan
    DORMANT = "dormant"  # probes exhausted; go silent until the member returns


def decide(
    plan: CatchupPlan,
    *,
    now: dt.datetime,
    local_now: dt.datetime,
    last_local: dt.datetime,
    in_quiet: bool,
) -> TickAction:
    """The whole nudge policy for one plan, in one pure function.

    `now` is UTC-aware or naive but consistent with the plan's timestamps;
    `local_now`/`last_local` are the group-local wall clock for the quiet-hours
    and usual-hour rules. The caller claims in the database whatever this
    returns before sending anything.
    """
    if in_quiet:
        return TickAction.SKIP

    if plan.state == CatchupState.PAUSED:
        if plan.paused_by_user:
            return TickAction.SKIP
        if should_go_dormant(plan.probe_count):
            return TickAction.DORMANT
        return TickAction.PROBE if probe_due(plan.last_probe_at, now) else TickAction.SKIP

    if plan.state != CatchupState.ACTIVE:
        return TickAction.SKIP

    if not plan.queue:
        return TickAction.SKIP

    if should_auto_pause(plan.ignored_streak):
        return TickAction.PAUSE_NOTICE

    last_utc = plan.last_done_at or plan.last_sent_at or plan.enrolled_at
    if plan.pending_start is None:
        if now - last_utc < NEXT_PIECE_GAP:
            return TickAction.SKIP
        # The first piece of a new day waits for the hour the member usually
        # reads at, once there is a pattern to learn from.
        if (
            plan.done_hours
            and last_local.date() < local_now.date()
            and local_now.hour < best_hour(plan.done_hours)
        ):
            return TickAction.SKIP
        return TickAction.PIECE

    if plan.last_sent_at is None or now - plan.last_sent_at < nudge_gap(plan.ignored_streak):
        return TickAction.SKIP
    return TickAction.NUDGE
