"""Repository layer tests against a real (throwaway) database."""

from __future__ import annotations

import datetime as dt

from quran_wird.db.models import AdvanceRule, CompletionSource, utcnow
from quran_wird.db.repo import (
    GroupRepo,
    MediaRepo,
    NudgeRepo,
    StatsRepo,
    SubscriberRepo,
    TaskRepo,
)
from quran_wird.domain.schemas import GroupSettingsPatch

CHAT = -100123
TODAY = dt.date(2026, 9, 6)


async def make_group(session, chat_id: int = CHAT):
    group, _ = await GroupRepo(session).get_or_create(chat_id, title="مجموعة الورد")
    return group


class TestGroupRepo:
    async def test_defaults_match_the_agreed_settings(self, session):
        group = await make_group(session)
        assert group.pages_per_day == 2
        assert group.send_time == dt.time(5, 0)
        assert group.first_reminder_after_hours == 8
        assert group.timezone == "Asia/Damascus"
        assert group.week_start_weekday == 5  # Saturday
        assert group.advance_rule is AdvanceRule.ANYONE
        assert group.current_page == 1
        assert group.khatmah_number == 1

    async def test_get_or_create_is_idempotent(self, session):
        _, created_first = await GroupRepo(session).get_or_create(CHAT)
        _, created_again = await GroupRepo(session).get_or_create(CHAT)
        assert created_first is True
        assert created_again is False

    async def test_recreating_keeps_progress(self, session):
        repo = GroupRepo(session)
        await repo.get_or_create(CHAT)
        await repo.set_current_page(CHAT, 250)
        # Re-adding the bot must resume the khatmah, not restart it.
        group, created = await repo.get_or_create(CHAT)
        assert created is False
        assert group.current_page == 250

    async def test_title_refreshes_on_contact(self, session):
        repo = GroupRepo(session)
        await repo.get_or_create(CHAT, title="الاسم القديم")
        group, _ = await repo.get_or_create(CHAT, title="الاسم الجديد")
        assert group.title == "الاسم الجديد"

    async def test_apply_settings_touches_only_given_fields(self, session):
        repo = GroupRepo(session)
        await repo.get_or_create(CHAT)
        group = await repo.apply_settings(CHAT, GroupSettingsPatch(pages_per_day=5))
        assert group.pages_per_day == 5
        assert group.send_time == dt.time(5, 0)  # untouched

    async def test_list_active_excludes_deactivated(self, session):
        repo = GroupRepo(session)
        await repo.get_or_create(CHAT)
        await repo.get_or_create(-200)
        await repo.set_active(-200, False)
        assert [g.chat_id for g in await repo.list_active()] == [CHAT]

    async def test_start_new_khatmah(self, session):
        repo = GroupRepo(session)
        await repo.get_or_create(CHAT)
        await repo.set_current_page(CHAT, 604)
        group = await repo.start_new_khatmah(CHAT, on=TODAY)
        assert group.khatmah_number == 2
        assert group.current_page == 1
        assert group.khatmah_started_on == TODAY


class TestSubscriberRepo:
    async def test_subscribe_and_count(self, session):
        await make_group(session)
        repo = SubscriberRepo(session)
        _, new = await repo.subscribe(CHAT, 1, display_name="أحمد")
        assert new is True
        assert await repo.count_active(CHAT) == 1

    async def test_subscribing_twice_is_not_new(self, session):
        await make_group(session)
        repo = SubscriberRepo(session)
        await repo.subscribe(CHAT, 1, display_name="أحمد")
        _, new = await repo.subscribe(CHAT, 1, display_name="أحمد")
        assert new is False
        assert await repo.count_active(CHAT) == 1

    async def test_leaving_then_rejoining_keeps_the_same_row(self, session):
        await make_group(session)
        repo = SubscriberRepo(session)
        await repo.subscribe(CHAT, 1, display_name="أحمد")
        assert await repo.unsubscribe(CHAT, 1) is True
        assert await repo.count_active(CHAT) == 0

        sub, new = await repo.subscribe(CHAT, 1, display_name="أحمد")
        assert new is True  # newly active again
        assert sub.left_at is None
        assert await repo.count_active(CHAT) == 1

    async def test_unsubscribe_twice_returns_false(self, session):
        await make_group(session)
        repo = SubscriberRepo(session)
        await repo.subscribe(CHAT, 1, display_name="أحمد")
        assert await repo.unsubscribe(CHAT, 1) is True
        assert await repo.unsubscribe(CHAT, 1) is False

    async def test_unsubscribe_unknown_user(self, session):
        await make_group(session)
        assert await SubscriberRepo(session).unsubscribe(CHAT, 999) is False

    async def test_name_is_refreshed(self, session):
        await make_group(session)
        repo = SubscriberRepo(session)
        await repo.subscribe(CHAT, 1, display_name="أحمد")
        await repo.touch_name(CHAT, 1, display_name="أحمد الحسن", username="ahmad")
        sub = await repo.get(CHAT, 1)
        assert sub.display_name == "أحمد الحسن"
        assert sub.username == "ahmad"

    async def test_groups_are_independent(self, session):
        await make_group(session)
        await make_group(session, -200)
        repo = SubscriberRepo(session)
        await repo.subscribe(CHAT, 1, display_name="أحمد")
        assert await repo.count_active(CHAT) == 1
        assert await repo.count_active(-200) == 0


class TestNudgeRepo:
    async def test_first_nudge_is_due(self, session):
        assert await NudgeRepo(session).due(CHAT, 1) is True

    async def test_not_due_again_immediately(self, session):
        repo = NudgeRepo(session)
        await repo.record(CHAT, 1)
        assert await repo.due(CHAT, 1) is False

    async def test_due_again_after_a_week(self, session):
        repo = NudgeRepo(session)
        await repo.record(CHAT, 1)
        assert await repo.due(CHAT, 1, now=utcnow() + dt.timedelta(days=8)) is True


class TestTaskRepo:
    async def _task(self, session):
        await make_group(session)
        return await TaskRepo(session).create(CHAT, task_date=TODAY, page_start=1, page_end=2)

    async def test_create_and_fetch_by_date(self, session):
        task = await self._task(session)
        found = await TaskRepo(session).get_by_date(CHAT, TODAY)
        assert found.id == task.id
        assert found.page_count == 2

    async def test_open_task_lookup(self, session):
        task = await self._task(session)
        assert (await TaskRepo(session).get_open(CHAT)).id == task.id

    async def test_closing_removes_it_from_open(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        await repo.close(task.id)
        assert await repo.get_open(CHAT) is None

    async def test_mark_done_is_idempotent(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        assert await repo.mark_done(task.id, 1, was_subscriber=True) is True
        # Pressing the button twice must not count twice.
        assert await repo.mark_done(task.id, 1, was_subscriber=True) is False
        assert await repo.done_count(task.id) == 1

    async def test_done_user_ids_in_completion_order(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        for uid in (7, 3, 5):
            await repo.mark_done(task.id, uid, was_subscriber=True)
        assert await repo.done_user_ids(task.id) == [7, 3, 5]

    async def test_undo_done(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        await repo.mark_done(task.id, 1, was_subscriber=True)
        assert await repo.undo_done(task.id, 1) is True
        assert await repo.undo_done(task.id, 1) is False
        assert await repo.done_count(task.id) == 0

    async def test_non_subscriber_completion_is_flagged(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        await repo.mark_done(task.id, 99, was_subscriber=False, source=CompletionSource.COMMAND)
        assert await repo.has_done(task.id, 99) is True

    async def test_pending_subscribers_excludes_finishers(self, session):
        task = await self._task(session)
        subs = SubscriberRepo(session)
        for uid, name in ((1, "أحمد"), (2, "محمد"), (3, "سارة")):
            await subs.subscribe(CHAT, uid, display_name=name)

        repo = TaskRepo(session)
        await repo.mark_done(task.id, 2, was_subscriber=True)
        pending = await repo.pending_subscribers(task.id)
        assert {s.user_id for s in pending} == {1, 3}

    async def test_pending_excludes_inactive_subscribers(self, session):
        task = await self._task(session)
        subs = SubscriberRepo(session)
        await subs.subscribe(CHAT, 1, display_name="أحمد")
        await subs.subscribe(CHAT, 2, display_name="محمد")
        await subs.unsubscribe(CHAT, 2)

        pending = await TaskRepo(session).pending_subscribers(task.id)
        assert {s.user_id for s in pending} == {1}

    async def test_non_subscriber_never_appears_as_pending(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        # Someone who pressed the button without joining is not chased for it.
        await repo.mark_done(task.id, 99, was_subscriber=False)
        assert await repo.pending_subscribers(task.id) == []

    async def test_reminder_log_blocks_a_repeat(self, session):
        task = await self._task(session)
        repo = TaskRepo(session)
        assert await repo.log_reminder(task.id, 1) is True
        # A restart replays the job; the log is what prevents a double ping.
        assert await repo.log_reminder(task.id, 1) is False
        assert await repo.log_reminder(task.id, 2) is True

    async def test_list_between_is_inclusive_and_ordered(self, session):
        await make_group(session)
        repo = TaskRepo(session)
        for offset in range(4):
            day = TODAY + dt.timedelta(days=offset)
            await repo.create(CHAT, task_date=day, page_start=1, page_end=2)

        window = await repo.list_between(CHAT, TODAY, TODAY + dt.timedelta(days=2))
        assert [t.task_date for t in window] == [
            TODAY,
            TODAY + dt.timedelta(days=1),
            TODAY + dt.timedelta(days=2),
        ]


class TestStatsRepo:
    async def test_streak_grows_across_days(self, session):
        repo = StatsRepo(session)
        for offset in range(3):
            await repo.record_done(CHAT, 1, TODAY + dt.timedelta(days=offset))
        stats = await repo.get(CHAT, 1)
        assert (stats.total_done, stats.current_streak, stats.best_streak) == (3, 3, 3)

    async def test_same_day_twice_does_not_inflate(self, session):
        repo = StatsRepo(session)
        await repo.record_done(CHAT, 1, TODAY)
        await repo.record_done(CHAT, 1, TODAY)
        stats = await repo.get(CHAT, 1)
        assert (stats.total_done, stats.current_streak) == (1, 1)

    async def test_missing_breaks_the_streak_but_keeps_the_best(self, session):
        repo = StatsRepo(session)
        await repo.record_done(CHAT, 1, TODAY)
        await repo.record_done(CHAT, 1, TODAY + dt.timedelta(days=1))
        await repo.record_missed(CHAT, 1)
        stats = await repo.get(CHAT, 1)
        assert stats.current_streak == 0
        assert stats.best_streak == 2
        assert stats.consecutive_missed == 1
        assert stats.total_missed == 1

    async def test_returning_after_a_miss_resets_consecutive_missed(self, session):
        repo = StatsRepo(session)
        await repo.record_missed(CHAT, 1)
        await repo.record_missed(CHAT, 1)
        await repo.record_done(CHAT, 1, TODAY)
        stats = await repo.get(CHAT, 1)
        assert stats.consecutive_missed == 0
        assert stats.current_streak == 1

    async def test_leaderboard_ranks_by_streak(self, session):
        repo = StatsRepo(session)
        await repo.record_done(CHAT, 1, TODAY)
        for offset in range(3):
            await repo.record_done(CHAT, 2, TODAY + dt.timedelta(days=offset))
        board = await repo.leaderboard(CHAT)
        assert [s.user_id for s in board] == [2, 1]

    async def test_top_streak_ignores_zero_streaks(self, session):
        repo = StatsRepo(session)
        await repo.record_missed(CHAT, 1)
        assert await repo.top_streak(CHAT) is None


class TestMediaRepo:
    async def test_remember_and_read_back(self, session):
        repo = MediaRepo(session)
        await repo.remember(1, "FILE_ID_1")
        assert await repo.get_file_id(1) == "FILE_ID_1"

    async def test_unknown_page_has_no_id(self, session):
        assert await MediaRepo(session).get_file_id(42) is None

    async def test_remember_overwrites(self, session):
        repo = MediaRepo(session)
        await repo.remember(1, "OLD")
        await repo.remember(1, "NEW")
        assert await repo.get_file_id(1) == "NEW"

    async def test_get_many_returns_only_known_pages(self, session):
        repo = MediaRepo(session)
        await repo.remember(1, "A")
        await repo.remember(3, "C")
        assert await repo.get_many([1, 2, 3]) == {1: "A", 3: "C"}

    async def test_get_many_with_no_pages(self, session):
        assert await MediaRepo(session).get_many([]) == {}

    async def test_forget_drops_a_stale_id(self, session):
        repo = MediaRepo(session)
        await repo.remember(1, "STALE")
        await repo.forget(1)
        assert await repo.get_file_id(1) is None
