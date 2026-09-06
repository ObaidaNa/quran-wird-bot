"""The weekly honors board's arithmetic.

Pure functions over plain records, so the week boundaries and the "no day
missed" rule can be tested exhaustively without a database or a bot.

Two rules decided here, both from docs/PLAN.md section 5:

  * **A member is judged only on the days they were already subscribed.**
    Someone who joins on Wednesday is not marked down for Saturday — nor handed
    a badge they did not earn.
  * **A repeated wird still counts as a day.** `close_day` records a missed day
    for every task including repeats, so the board has to agree with the streaks
    or the two would tell members different stories. Repeats are excluded from
    the *pages* figure instead, since re-reading page 5 does not move a khatmah.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .schemas import TOTAL_PAGES


@dataclass(frozen=True)
class WeekTask:
    """One day's wird as the report sees it."""

    task_date: dt.date
    page_count: int
    done_user_ids: frozenset[int]
    is_repeat: bool = False


@dataclass(frozen=True)
class WeekMember:
    """A subscriber, with the day they joined — the day they became answerable."""

    user_id: int
    display_name: str
    joined_on: dt.date


@dataclass(frozen=True)
class MemberWeek:
    member: WeekMember
    done: int
    eligible: int

    @property
    def perfect(self) -> bool:
        return self.eligible > 0 and self.done == self.eligible


@dataclass
class WeekSummary:
    """Everything the honors board reports about one week."""

    active_days: int = 0
    pages_read: int = 0
    completions: int = 0
    possible_completions: int = 0
    members: list[MemberWeek] = field(default_factory=list)

    @property
    def perfect(self) -> list[MemberWeek]:
        return [m for m in self.members if m.perfect]

    @property
    def completion_rate(self) -> float:
        if not self.possible_completions:
            return 0.0
        return self.completions / self.possible_completions * 100

    @property
    def everyone_perfect(self) -> bool:
        """Whether every member read every day they were answerable for.

        A week with nothing to read is not a perfect week: without the first
        check, a group that paused for a week would be congratulated for it.
        """
        return self.possible_completions > 0 and self.completions == self.possible_completions


def week_bounds(day: dt.date, week_start_weekday: int) -> tuple[dt.date, dt.date]:
    """The inclusive (start, end) of the week containing `day`.

    `week_start_weekday` is a `date.weekday()` value, so the default Saturday
    start is 5 and the week it produces runs Saturday to Friday.
    """
    offset = (day.weekday() - week_start_weekday) % 7
    start = day - dt.timedelta(days=offset)
    return start, start + dt.timedelta(days=6)


def summarise(tasks: list[WeekTask], members: list[WeekMember]) -> WeekSummary:
    """Fold one week's tasks and subscribers into the board's figures."""
    summary = WeekSummary(
        active_days=len(tasks),
        pages_read=sum(t.page_count for t in tasks if not t.is_repeat),
    )

    for member in members:
        eligible = [t for t in tasks if t.task_date >= member.joined_on]
        done = sum(1 for t in eligible if member.user_id in t.done_user_ids)
        summary.members.append(MemberWeek(member=member, done=done, eligible=len(eligible)))
        summary.completions += done
        summary.possible_completions += len(eligible)

    # Best week first, then the longest-serving member, so the order is stable.
    summary.members.sort(key=lambda m: (-m.done, m.member.joined_on, m.member.user_id))
    return summary


def weeks_to_finish(current_page: int, pages_this_week: int) -> int | None:
    """Weeks left in the khatmah at this week's pace, or None if it stalled."""
    if pages_this_week <= 0:
        return None
    pages_left = max(0, TOTAL_PAGES - (current_page - 1))
    return -(-pages_left // pages_this_week)
