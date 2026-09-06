"""Page-range and advance-rule tests."""

from __future__ import annotations

import pytest

from quran_wird.db.models import AdvanceRule
from quran_wird.domain.progress import advance_from, next_range, progress_bar, should_advance


class TestNextRange:
    def test_two_pages_from_the_start(self):
        r = next_range(1, 2)
        assert (r.start, r.end) == (1, 2)

    def test_single_page_per_day(self):
        r = next_range(300, 1)
        assert (r.start, r.end) == (300, 300)

    def test_clamps_at_the_end_of_the_mushaf(self):
        # A five-page group starting at 602 must not ask for page 606.
        r = next_range(602, 5)
        assert (r.start, r.end) == (602, 604)
        assert r.count == 3

    def test_last_page_alone(self):
        r = next_range(604, 2)
        assert (r.start, r.end) == (604, 604)

    def test_rejects_zero_pages_per_day(self):
        with pytest.raises(ValueError):
            next_range(1, 0)


class TestAdvanceFrom:
    def test_normal_advance(self):
        assert advance_from(2) == (3, False)

    def test_completing_the_mushaf_wraps_to_page_one(self):
        assert advance_from(604) == (1, True)


class TestShouldAdvance:
    @pytest.mark.parametrize("done", [0, 1, 5])
    def test_always_advances_regardless(self, done):
        assert should_advance(AdvanceRule.ALWAYS, done, 5)

    def test_anyone_needs_one_reader(self):
        assert should_advance(AdvanceRule.ANYONE, 1, 7)
        assert not should_advance(AdvanceRule.ANYONE, 0, 7)

    def test_all_needs_everyone(self):
        assert should_advance(AdvanceRule.ALL, 7, 7)
        assert not should_advance(AdvanceRule.ALL, 6, 7)

    def test_majority_is_strictly_more_than_half(self):
        assert should_advance(AdvanceRule.MAJORITY, 4, 7)
        assert not should_advance(AdvanceRule.MAJORITY, 3, 7)
        # An even split is not a majority.
        assert not should_advance(AdvanceRule.MAJORITY, 3, 6)
        assert should_advance(AdvanceRule.MAJORITY, 4, 6)

    @pytest.mark.parametrize("rule", [AdvanceRule.ANYONE, AdvanceRule.ALL, AdvanceRule.MAJORITY])
    def test_empty_group_does_not_advance(self, rule):
        # Without this, a group with no subscribers would burn through the mushaf.
        assert not should_advance(rule, 0, 0)


class TestProgressBar:
    def test_empty_at_the_start(self):
        assert progress_bar(1) == "▱" * 10

    def test_full_on_the_last_page(self):
        assert progress_bar(605) == "▰" * 10

    def test_halfway(self):
        assert progress_bar(303) == "▰" * 5 + "▱" * 5

    def test_width_is_respected(self):
        assert len(progress_bar(200, width=20)) == 20
