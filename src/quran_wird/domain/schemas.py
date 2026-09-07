"""Pydantic schemas: input validation and transport between layers.

The SQLAlchemy models describe storage; these describe the *contracts*:
  * `GroupSettingsPatch` validates what an admin submits from the `/settings`
    panel before any of it reaches the database.
  * The rest are display-ready payloads handed to the message layer, which keeps
    the text builders free of query logic and easy to test.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..db.models import AdvanceRule

TOTAL_PAGES = 604

Weekday = Annotated[int, Field(ge=0, le=6)]  # 0=Monday … 6=Sunday
PageNo = Annotated[int, Field(ge=1, le=TOTAL_PAGES)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# =============================== Settings ===============================


class GroupSettingsPatch(BaseModel):
    """A partial update to a group's settings; every field is optional.

    The bounds are not cosmetic: a `pages_per_day` above 10 exceeds Telegram's
    limit for a single media group, and a large `mentions_per_message` turns a
    reminder into a notification barrage.
    """

    model_config = ConfigDict(extra="forbid")

    timezone: str | None = None
    pages_per_day: Annotated[int, Field(ge=1, le=20)] | None = None
    active_weekdays: list[Weekday] | None = None
    send_time: dt.time | None = None
    pin_wird: bool | None = None

    first_reminder_after_hours: Annotated[int, Field(ge=1, le=23)] | None = None
    reminder_interval_hours: Annotated[int, Field(ge=1, le=12)] | None = None
    reminder_max_count: Annotated[int, Field(ge=0, le=6)] | None = None
    quiet_hours_start: dt.time | None = None
    quiet_hours_end: dt.time | None = None
    mentions_per_message: Annotated[int, Field(ge=1, le=10)] | None = None

    day_close_time: dt.time | None = None
    advance_rule: AdvanceRule | None = None

    weekly_report_enabled: bool | None = None
    weekly_report_weekday: Weekday | None = None
    weekly_report_time: dt.time | None = None
    week_start_weekday: Weekday | None = None

    current_page: PageNo | None = None
    is_active: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {v}") from exc
        return v

    @field_validator("active_weekdays")
    @classmethod
    def _at_least_one_day(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and not v:
            raise ValueError("at least one active weekday is required")
        return sorted(set(v)) if v else v

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


class GroupSettingsView(ORMModel):
    """Settings as rendered in the `/settings` panel."""

    chat_id: int
    title: str | None
    timezone: str
    pages_per_day: int
    active_weekdays: list[int]
    send_time: dt.time
    pin_wird: bool
    first_reminder_after_hours: int
    reminder_interval_hours: int
    reminder_max_count: int
    quiet_hours_start: dt.time | None
    quiet_hours_end: dt.time | None
    mentions_per_message: int
    day_close_time: dt.time
    advance_rule: AdvanceRule
    weekly_report_enabled: bool
    weekly_report_weekday: int
    weekly_report_time: dt.time
    week_start_weekday: int
    current_page: int
    khatmah_number: int
    is_active: bool


# ================================= Wird =================================


class PageRange(BaseModel):
    """The page range for a single day's wird."""

    start: PageNo
    end: PageNo

    @model_validator(mode="after")
    def _ordered(self) -> PageRange:
        if self.end < self.start:
            raise ValueError("range ends before it starts")
        return self

    @property
    def count(self) -> int:
        return self.end - self.start + 1

    @property
    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))


class PageInfo(ORMModel):
    """What a page contains, from the page_index table."""

    page_no: int
    juz: int
    hizb: int
    surah_names: str
    first_ayah: int
    last_ayah: int


class WirdView(BaseModel):
    """Everything the daily wird message needs to render."""

    task_id: int
    task_date: dt.date
    pages: PageRange
    juz: int
    surah_names: str
    is_repeat: bool = False
    # How many finished the wird this one repeats; 0 means nobody did, which is
    # the difference between "we repeat because nobody read" and "because not
    # everyone did" under the stricter advance rules.
    repeat_done_count: int = 0
    done_names: list[str] = Field(default_factory=list)
    subscriber_count: int = 0

    @property
    def done_count(self) -> int:
        return len(self.done_names)


# ============================ Owner panel ==============================


class GroupDigest(BaseModel):
    """One group as the owner panel lists it — never shown inside a group."""

    chat_id: int
    title: str | None = None
    is_active: bool = True
    subscribers: int = 0
    current_page: int = 1
    khatmah_number: int = 1
    last_wird: dt.date | None = None

    @property
    def pages_read(self) -> int:
        """This group's whole journey: finished khatmahs plus the current one."""
        return (self.khatmah_number - 1) * TOTAL_PAGES + self.current_page - 1

    @property
    def percent(self) -> int:
        """How far into the current khatmah, as a whole number."""
        return round((self.current_page - 1) / TOTAL_PAGES * 100)


class OwnerStats(BaseModel):
    """The whole fleet in one payload, gathered in a single pass.

    Everything here is a count the owner reads to judge whether the bot is
    growing and whether the groups it holds are actually reading.
    """

    generated_at: dt.datetime

    groups_total: int = 0
    groups_active: int = 0
    groups_new_week: int = 0
    groups_new_month: int = 0
    groups_reading_week: int = 0

    unique_users: int = 0
    subscriptions_total: int = 0
    subscriptions_active: int = 0
    members_on_streak: int = 0
    longest_streak: int = 0
    days_done: int = 0
    days_missed: int = 0

    khatmahs_completed: int = 0
    pages_read: int = 0

    wirds_total: int = 0
    wirds_today: int = 0
    wirds_week: int = 0
    completions_total: int = 0
    completions_today: int = 0
    completions_week: int = 0

    @property
    def groups_dormant(self) -> int:
        """Registered but not active: the bot was removed, or the group paused it."""
        return max(0, self.groups_total - self.groups_active)

    @property
    def khatmahs_running(self) -> int:
        """One shared khatmah per active group, by design (PLAN section 11)."""
        return self.groups_active

    @property
    def khatmahs_equivalent(self) -> int:
        """Pages read across the fleet, expressed as whole mushafs."""
        return self.pages_read // TOTAL_PAGES

    @property
    def completion_rate(self) -> float:
        """Share of expected readings that were actually marked done."""
        expected = self.days_done + self.days_missed
        return self.days_done / expected * 100 if expected else 0.0

    @property
    def members_per_group(self) -> float:
        return self.subscriptions_active / self.groups_active if self.groups_active else 0.0


class ReplaceOffer(BaseModel):
    """Today's already-sent wird next to the one the settings now describe.

    A group almost never joins the bot at page 1: the wird that goes out the
    moment the bot is added is a guess, and correcting it with `/setpage` used to
    leave the group looking at the wrong pages until the next morning. This is
    what the admin is asked to confirm before that wird is taken back.
    """

    task_id: int
    current: PageRange
    proposed: PageRange
    done_count: int = 0

    @property
    def differs(self) -> bool:
        """Whether replacing would actually change what the group is reading."""
        return (self.current.start, self.current.end) != (
            self.proposed.start,
            self.proposed.end,
        )


class DaySummaryView(BaseModel):
    """Payload for the short report posted when the day closes.

    `done_names` holds the *subscribers* who finished; guests who pressed the
    button are deliberately left out, because the rate and the advance rules are
    both measured against the subscriber list.
    """

    task_date: dt.date
    pages: PageRange
    done_names: list[str] = Field(default_factory=list)
    subscriber_count: int = 0
    advanced: bool = False
    next_pages: PageRange | None = None
    top_streak_name: str | None = None
    top_streak_days: int = 0
    khatmah_completed: bool = False

    @property
    def done_count(self) -> int:
        return len(self.done_names)

    @property
    def completion_rate(self) -> float:
        if not self.subscriber_count:
            return 0.0
        return self.done_count / self.subscriber_count * 100


# ========================= Members and reports =========================


class MemberProgress(ORMModel):
    """Member stats; shown by /me and used to pick the missed-days phrasing."""

    user_id: int
    display_name: str = ""
    total_done: int = 0
    total_missed: int = 0
    current_streak: int = 0
    best_streak: int = 0
    consecutive_missed: int = 0
    last_done_date: dt.date | None = None


class KhatmahProgress(BaseModel):
    """The group's progress toward completing the mushaf."""

    khatmah_number: int
    current_page: PageNo
    started_on: dt.date | None = None

    @property
    def pages_done(self) -> int:
        return self.current_page - 1

    @property
    def pages_left(self) -> int:
        return TOTAL_PAGES - self.pages_done

    @property
    def percent(self) -> float:
        return self.pages_done / TOTAL_PAGES * 100


class WeeklyReportData(BaseModel):
    """Payload for the weekly honors board."""

    week_start: dt.date
    week_end: dt.date
    active_days: int
    pages_read: int
    perfect_members: list[MemberProgress] = Field(default_factory=list)
    top_streak: MemberProgress | None = None
    khatmah: KhatmahProgress
    completions: int = 0
    possible_completions: int = 0
    weeks_to_finish: int | None = None

    @property
    def completion_rate(self) -> float:
        if not self.possible_completions:
            return 0.0
        return self.completions / self.possible_completions * 100

    @property
    def everyone_perfect(self) -> bool:
        return bool(self.perfect_members) and self.completions == self.possible_completions
