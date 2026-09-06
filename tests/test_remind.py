"""Reminders: quiet hours, timing, batching, mentions, and missed-day notices."""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot

from quran_wird.db.models import PageIndex
from quran_wird.db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.jobs.remind import (
    MISSED_NOTICE_THRESHOLD,
    in_quiet_hours,
    reminder_times,
    send_reminder,
)

CHAT = -100123
TODAY = dt.date.today()


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    # Quiet hours off, deliberately: they default to 23:00-07:00, and a suite
    # that inherits them fails for eight hours every night on correct code.
    # The skipping behaviour is tested on its own below, with a window built
    # around the current time so it holds whatever the hour.
    g.quiet_hours_start = None
    g.quiet_hours_end = None
    for page in range(1, 5):
        session.add(
            PageIndex(
                page_no=page,
                juz=1,
                hizb=1,
                first_surah=1,
                first_ayah=1,
                last_surah=1,
                last_ayah=7,
                surah_names="الفاتحة",
                ayah_count=7,
            )
        )
    await session.flush()
    return g


@pytest.fixture
async def task(session, group):
    return await TaskRepo(session).create(CHAT, task_date=TODAY, page_start=1, page_end=2)


async def subscribe(session, *names: str) -> list[int]:
    repo = SubscriberRepo(session)
    ids = []
    for index, name in enumerate(names, start=1):
        await repo.subscribe(CHAT, index, display_name=name)
        ids.append(index)
    return ids


class TestQuietHours:
    def test_window_wrapping_midnight(self):
        start, end = dt.time(23, 0), dt.time(7, 0)
        assert in_quiet_hours(dt.time(23, 30), start, end)
        assert in_quiet_hours(dt.time(3, 0), start, end)
        assert in_quiet_hours(dt.time(6, 59), start, end)

    def test_outside_the_wrapping_window(self):
        start, end = dt.time(23, 0), dt.time(7, 0)
        assert not in_quiet_hours(dt.time(7, 0), start, end)
        assert not in_quiet_hours(dt.time(13, 0), start, end)
        assert not in_quiet_hours(dt.time(22, 59), start, end)

    def test_window_within_one_day(self):
        start, end = dt.time(1, 0), dt.time(5, 0)
        assert in_quiet_hours(dt.time(3, 0), start, end)
        assert not in_quiet_hours(dt.time(23, 0), start, end)

    def test_disabled_when_unset_or_empty(self):
        assert not in_quiet_hours(dt.time(3, 0), None, dt.time(7, 0))
        assert not in_quiet_hours(dt.time(3, 0), dt.time(7, 0), None)
        assert not in_quiet_hours(dt.time(3, 0), dt.time(7, 0), dt.time(7, 0))

    async def test_reminder_is_skipped_during_quiet_hours(self, session, deps, task, group):
        await subscribe(session, "أحمد")
        # A window covering the whole day, so "now" is always inside it.
        group.quiet_hours_start = dt.time(0, 0)
        group.quiet_hours_end = dt.time(23, 59)
        await session.flush()

        bot = FakeBot()
        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 0
        assert bot.sent_texts == []


class TestReminderTimes:
    async def test_default_schedule_is_eight_then_every_three_hours(self, session, group):
        sent = dt.datetime(2026, 9, 5, 2, 0)  # 05:00 Damascus
        times = reminder_times(group, sent)
        assert [t.hour for t in times] == [10, 13, 16]  # +8, +11, +14 UTC

    async def test_count_follows_reminder_max_count(self, session, group):
        group.reminder_max_count = 1
        assert len(reminder_times(group, dt.datetime(2026, 9, 5, 2, 0))) == 1

    async def test_zero_reminders_is_allowed(self, session, group):
        group.reminder_max_count = 0
        assert reminder_times(group, dt.datetime(2026, 9, 5, 2, 0)) == []


class TestSendReminder:
    async def test_mentions_pending_members(self, session, deps, task):
        await subscribe(session, "أحمد", "سارة")
        bot = FakeBot()

        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 1
        text = bot.sent_texts[0]
        assert "tg://user?id=1" in text
        assert "tg://user?id=2" in text
        assert "تذكير بورد اليوم" in text

    async def test_finishers_are_not_mentioned(self, session, deps, task):
        await subscribe(session, "أحمد", "سارة")
        await TaskRepo(session).mark_done(task.id, 1, was_subscriber=True)

        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        text = bot.sent_texts[0]
        assert "tg://user?id=1" not in text
        assert "tg://user?id=2" in text
        assert "أنجز ١ من ٢" in text

    async def test_batches_of_four_by_default(self, session, deps, task):
        await subscribe(session, *[f"عضو {i}" for i in range(1, 10)])  # nine members
        bot = FakeBot()

        # Nine pending at four per message -> three messages.
        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 3

    async def test_batch_size_is_configurable(self, session, deps, task, group):
        await subscribe(session, *[f"عضو {i}" for i in range(1, 7)])
        group.mentions_per_message = 2
        await session.flush()

        assert await send_reminder(FakeBot(), deps, CHAT, task.id, 1) == 3

    async def test_nothing_sent_when_everyone_finished(self, session, deps, task):
        ids = await subscribe(session, "أحمد", "سارة")
        for uid in ids:
            await TaskRepo(session).mark_done(task.id, uid, was_subscriber=True)

        bot = FakeBot()
        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 0
        assert bot.sent_texts == []

    async def test_second_call_is_blocked_by_the_log(self, session, deps, task):
        await subscribe(session, "أحمد")
        bot = FakeBot()

        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 1
        # A restart replays the job; the reminder must not go out twice.
        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 0
        assert len(bot.sent_texts) == 1

    async def test_different_sequences_are_independent(self, session, deps, task):
        await subscribe(session, "أحمد")
        bot = FakeBot()
        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 1
        assert await send_reminder(bot, deps, CHAT, task.id, 2) == 1

    async def test_closed_task_is_skipped(self, session, deps, task):
        await subscribe(session, "أحمد")
        await TaskRepo(session).close(task.id)
        assert await send_reminder(FakeBot(), deps, CHAT, task.id, 1) == 0

    async def test_inactive_group_is_skipped(self, session, deps, task, group):
        await subscribe(session, "أحمد")
        group.is_active = False
        await session.flush()
        assert await send_reminder(FakeBot(), deps, CHAT, task.id, 1) == 0

    async def test_task_from_another_chat_is_refused(self, session, deps, task):
        await subscribe(session, "أحمد")
        assert await send_reminder(FakeBot(), deps, -999, task.id, 1) == 0

    async def test_guests_are_never_reminded(self, session, deps, task):
        await subscribe(session, "أحمد")
        await TaskRepo(session).mark_done(task.id, 99, was_subscriber=False)

        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        assert "tg://user?id=99" not in bot.sent_texts[0]

    async def test_names_are_html_escaped(self, session, deps, task):
        await SubscriberRepo(session).subscribe(CHAT, 1, display_name="<b>خطر</b>")
        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        assert "&lt;b&gt;" in bot.sent_texts[0]


class TestQuietHoursSkip:
    async def test_a_reminder_inside_the_quiet_window_is_skipped(self, session, deps, task, group):
        # The window is built around now, so this holds at any hour.
        from zoneinfo import ZoneInfo

        now = dt.datetime.now(ZoneInfo(group.timezone))
        group.quiet_hours_start = (now - dt.timedelta(hours=1)).time()
        group.quiet_hours_end = (now + dt.timedelta(hours=1)).time()
        await subscribe(session, "أحمد")

        bot = FakeBot()
        assert await send_reminder(bot, deps, CHAT, task.id, 1) == 0
        assert bot.messages == []

    async def test_the_skipped_reminder_is_not_marked_as_sent(self, session, deps, task, group):
        # Skipping is not sending: the sequence must stay available so a later
        # reminder outside the window can still go out.
        from zoneinfo import ZoneInfo

        now = dt.datetime.now(ZoneInfo(group.timezone))
        group.quiet_hours_start = (now - dt.timedelta(hours=1)).time()
        group.quiet_hours_end = (now + dt.timedelta(hours=1)).time()
        await subscribe(session, "أحمد")

        await send_reminder(FakeBot(), deps, CHAT, task.id, 1)

        assert await TaskRepo(session).reminder_sent(task.id, 1) is False


class TestMissedNotices:
    async def test_notice_appears_past_the_threshold(self, session, deps, task):
        await subscribe(session, "أحمد")
        stats = await StatsRepo(session).get_or_create(CHAT, 1)
        stats.consecutive_missed = MISSED_NOTICE_THRESHOLD
        await session.flush()

        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        assert "🔸" in bot.sent_texts[0]

    async def test_a_single_missed_day_is_not_nagged(self, session, deps, task):
        await subscribe(session, "أحمد")
        stats = await StatsRepo(session).get_or_create(CHAT, 1)
        stats.consecutive_missed = 1
        await session.flush()

        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        assert "🔸" not in bot.sent_texts[0]

    async def test_no_notice_for_a_member_with_no_history(self, session, deps, task):
        await subscribe(session, "أحمد")
        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        assert "🔸" not in bot.sent_texts[0]

    async def test_notice_only_for_the_member_it_concerns(self, session, deps, task):
        await subscribe(session, "أحمد", "سارة")
        stats = await StatsRepo(session).get_or_create(CHAT, 2)
        stats.consecutive_missed = 4
        await session.flush()

        bot = FakeBot()
        await send_reminder(bot, deps, CHAT, task.id, 1)
        text = bot.sent_texts[0]
        assert text.count("🔸") == 1
        assert "سارة" in text.split("🔸")[1]
