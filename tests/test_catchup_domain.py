"""The catch-up plan's pure policy: queues, pieces, doses, and the tick's choice."""

from __future__ import annotations

import datetime as dt

from quran_wird.db.models import CatchupPlan, CatchupState
from quran_wird.domain import catchup as policy
from quran_wird.domain.catchup import TickAction


def plan(**fields) -> CatchupPlan:
    defaults = dict(
        chat_id=-100,
        user_id=7,
        state=CatchupState.ACTIVE,
        queue=[[4, 6]],
        debt_tasks=[1],
        cursor=10,
        dose=policy.START_DOSE,
        tone_index=0,
        ignored_streak=0,
        done_hours=[],
        pending_start=None,
        pending_end=None,
        pending_message_id=None,
        last_sent_at=None,
        last_done_at=None,
        paused_by_user=False,
        probe_count=0,
        last_probe_at=None,
        enrolled_at=dt.datetime(2026, 10, 1, 6, 0),
    )
    defaults.update(fields)
    return CatchupPlan(**defaults)


# --------------------------------------------------------------- page ranges


class TestMergeRanges:
    def test_overlapping_ranges_merge(self):
        assert policy.merge_ranges([[1, 3], [2, 5]]) == [[1, 5]]

    def test_adjacent_ranges_merge_so_a_repeat_costs_nothing_twice(self):
        assert policy.merge_ranges([[4, 5], [6, 8]]) == [[4, 8]]

    def test_disjoint_ranges_stay_separate(self):
        assert policy.merge_ranges([[1, 2], [5, 6]]) == [[1, 2], [5, 6]]


class TestDropPages:
    def test_reading_the_middle_splits_the_range(self):
        assert policy.drop_pages([[1, 10]], 4, 6) == [[1, 3], [7, 10]]

    def test_reading_outside_the_queue_changes_nothing(self):
        assert policy.drop_pages([[4, 6]], 10, 12) == [[4, 6]]

    def test_reading_the_whole_queue_empties_it(self):
        assert policy.drop_pages([[4, 6]], 4, 6) == []


# --------------------------------------------------------------- the piece


class TestNextPiece:
    def test_piece_is_dose_sized_within_the_span(self):
        assert policy.next_piece([(10, 30)], cursor=10, dose=5) == (10, 14)

    def test_piece_is_clamped_to_the_span_end(self):
        assert policy.next_piece([(10, 12)], cursor=10, dose=20) == (10, 12)

    def test_spans_behind_the_cursor_are_already_read(self):
        assert policy.next_piece([(1, 9), (10, 20)], cursor=12, dose=5) == (12, 16)

    def test_nothing_left_means_no_piece(self):
        assert policy.next_piece([(1, 9)], cursor=10, dose=5) is None


class TestAdvanceCursor:
    def test_completed_spans_drop_out(self):
        spans, cursor = policy.advance_cursor([(1, 9), (10, 20)], done_end=9)
        assert spans == [(10, 20)]
        assert cursor == 10

    def test_a_partial_span_keeps_its_unread_tail(self):
        spans, cursor = policy.advance_cursor([(1, 20)], done_end=5)
        assert spans == [(6, 20)]
        assert cursor == 6


# ------------------------------------------------------------------ the dose


class TestDose:
    def test_success_grows_the_dose_to_a_ceiling(self):
        dose = policy.MIN_DOSE
        for _ in range(20):
            dose = policy.dose_after_success(dose)
        assert dose == policy.MAX_DOSE

    def test_being_ignored_shrinks_the_dose_to_a_floor(self):
        dose = policy.MAX_DOSE
        for _ in range(20):
            dose = policy.dose_after_lapse(dose)
        assert dose == policy.MIN_DOSE


# ------------------------------------------------------------ the nudge gaps


class TestNudgeGap:
    def test_gaps_widen_as_nudges_go_unanswered(self):
        gaps = [policy.nudge_gap(i) for i in range(len(policy.NUDGE_GAPS_HOURS))]
        assert gaps == sorted(gaps)

    def test_beyond_the_table_the_gap_stops_growing(self):
        assert policy.nudge_gap(99) == policy.nudge_gap(len(policy.NUDGE_GAPS_HOURS) - 1)


class TestBestHour:
    def test_no_history_uses_the_default(self):
        assert policy.best_hour([]) == policy.DEFAULT_TARGET_HOUR

    def test_the_member_usual_hour_wins(self):
        assert policy.best_hour([21, 21, 9, 21, 9]) == 21


# ------------------------------------------------------------------- decide

NOW = dt.datetime(2026, 10, 22, 10, 0)  # 13:00 in Asia/Damascus — not quiet
LOCAL = dt.datetime(2026, 10, 22, 13, 0)


class TestDecide:
    def test_quiet_hours_silence_everything(self):
        p = plan(pending_start=10, pending_end=14, last_sent_at=NOW - dt.timedelta(days=2))
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=True)
            == TickAction.SKIP
        )

    def test_a_member_paused_plan_is_left_alone(self):
        p = plan(state=CatchupState.PAUSED, paused_by_user=True)
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.SKIP
        )

    def test_an_auto_paused_plan_gets_a_weekly_probe(self):
        p = plan(state=CatchupState.PAUSED, paused_by_user=False, last_probe_at=None)
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.PROBE
        )

    def test_exhausted_probes_make_the_plan_dormant(self):
        p = plan(
            state=CatchupState.PAUSED,
            paused_by_user=False,
            probe_count=policy.DORMANT_AFTER_PROBES,
        )
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.DORMANT
        )

    def test_seven_unanswered_nudges_pause_the_plan(self):
        p = plan(ignored_streak=policy.AUTO_PAUSE_AFTER)
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.PAUSE_NOTICE
        )

    def test_a_fresh_piece_goes_out_once_the_gap_passes(self):
        p = plan(pending_start=None, pending_end=None, last_done_at=None)
        p.enrolled_at = NOW - dt.timedelta(hours=2)
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.PIECE
        )

    def test_the_first_piece_of_a_day_waits_for_the_usual_hour(self):
        p = plan(
            pending_start=None,
            pending_end=None,
            last_done_at=NOW - dt.timedelta(days=1),
            done_hours=[21, 20, 21],
        )
        before = dt.datetime(2026, 10, 22, 13, 0)
        after = dt.datetime(2026, 10, 22, 21, 30)
        last_local = dt.datetime(2026, 10, 21, 21, 0)
        assert (
            policy.decide(p, now=NOW, local_now=before, last_local=last_local, in_quiet=False)
            == TickAction.SKIP
        )
        assert (
            policy.decide(p, now=NOW, local_now=after, last_local=last_local, in_quiet=False)
            == TickAction.PIECE
        )

    def test_a_pending_piece_is_nudged_only_after_its_gap(self):
        p = plan(pending_start=10, pending_end=14, last_sent_at=NOW - dt.timedelta(hours=2))
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.SKIP
        )
        p.last_sent_at = NOW - dt.timedelta(hours=4)
        assert (
            policy.decide(p, now=NOW, local_now=LOCAL, last_local=LOCAL, in_quiet=False)
            == TickAction.NUDGE
        )
