"""Pydantic schema tests: validating what arrives from the settings panel."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from quran_wird.db.models import AdvanceRule
from quran_wird.domain.schemas import (
    GroupSettingsPatch,
    KhatmahProgress,
    PageRange,
    WeeklyReportData,
)


class TestGroupSettingsPatch:
    def test_empty_patch_has_no_changes(self):
        assert GroupSettingsPatch().changes() == {}

    def test_only_set_fields_are_returned(self):
        patch = GroupSettingsPatch(pages_per_day=5)
        assert patch.changes() == {"pages_per_day": 5}

    @pytest.mark.parametrize("pages", [0, -1, 21])
    def test_rejects_out_of_range_pages(self, pages):
        with pytest.raises(ValidationError):
            GroupSettingsPatch(pages_per_day=pages)

    def test_rejects_unknown_field(self):
        # extra="forbid" stops a misspelled key from being silently ignored
        with pytest.raises(ValidationError):
            GroupSettingsPatch(pages_per_dayy=2)

    def test_rejects_unknown_timezone(self):
        with pytest.raises(ValidationError):
            GroupSettingsPatch(timezone="Mars/Olympus")

    def test_accepts_damascus(self):
        assert GroupSettingsPatch(timezone="Asia/Damascus").changes() == {
            "timezone": "Asia/Damascus"
        }

    def test_weekdays_deduplicated_and_sorted(self):
        patch = GroupSettingsPatch(active_weekdays=[5, 0, 5, 3])
        assert patch.changes()["active_weekdays"] == [0, 3, 5]

    def test_rejects_empty_weekdays(self):
        # A group with no active weekdays means a bot that never speaks again
        with pytest.raises(ValidationError):
            GroupSettingsPatch(active_weekdays=[])

    @pytest.mark.parametrize("day", [-1, 7])
    def test_rejects_invalid_weekday(self, day):
        with pytest.raises(ValidationError):
            GroupSettingsPatch(active_weekdays=[day])

    def test_rejects_page_beyond_mushaf(self):
        with pytest.raises(ValidationError):
            GroupSettingsPatch(current_page=605)

    def test_accepts_last_page(self):
        assert GroupSettingsPatch(current_page=604).changes() == {"current_page": 604}

    def test_advance_rule_from_string(self):
        assert GroupSettingsPatch(advance_rule="all").advance_rule is AdvanceRule.ALL

    def test_rejects_bad_advance_rule(self):
        with pytest.raises(ValidationError):
            GroupSettingsPatch(advance_rule="sometimes")

    def test_time_parsed_from_string(self):
        assert GroupSettingsPatch(send_time="05:00").send_time == dt.time(5, 0)


class TestPageRange:
    def test_count_and_pages(self):
        r = PageRange(start=120, end=121)
        assert r.count == 2
        assert r.pages == [120, 121]

    def test_single_page(self):
        r = PageRange(start=1, end=1)
        assert r.count == 1
        assert r.pages == [1]

    def test_rejects_reversed_range(self):
        with pytest.raises(ValidationError):
            PageRange(start=10, end=9)

    def test_rejects_page_zero(self):
        with pytest.raises(ValidationError):
            PageRange(start=0, end=2)


class TestKhatmahProgress:
    def test_fresh_khatmah(self):
        k = KhatmahProgress(khatmah_number=1, current_page=1)
        assert k.pages_done == 0
        assert k.pages_left == 604
        assert k.percent == 0

    def test_last_page_is_not_complete(self):
        # Page 604 is the *next* wird, so one page is still unread
        k = KhatmahProgress(khatmah_number=1, current_page=604)
        assert k.pages_done == 603
        assert k.pages_left == 1

    def test_percent(self):
        k = KhatmahProgress(khatmah_number=2, current_page=303)
        assert round(k.percent) == 50


class TestWeeklyReportData:
    def _report(self, **kw):
        defaults = dict(
            week_start=dt.date(2026, 8, 29),
            week_end=dt.date(2026, 9, 4),
            active_days=7,
            pages_read=14,
            khatmah=KhatmahProgress(khatmah_number=1, current_page=124),
        )
        return WeeklyReportData(**(defaults | kw))

    def test_completion_rate(self):
        r = self._report(completions=38, possible_completions=49)
        assert round(r.completion_rate) == 78

    def test_no_division_by_zero_on_empty_week(self):
        # A week with no subscribers must not blow up the report
        assert self._report(completions=0, possible_completions=0).completion_rate == 0.0

    def test_everyone_perfect_needs_members(self):
        # An empty honors board is not 'everyone perfect', even when the counters match
        assert not self._report(completions=0, possible_completions=0).everyone_perfect
