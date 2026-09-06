"""The weekly honors board: week boundaries, the perfect-week rule, and badges."""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot

from quran_wird.db.models import BadgeKind, PageIndex
from quran_wird.db.repo import (
    BadgeRepo,
    GroupRepo,
    StatsRepo,
    SubscriberRepo,
    TaskRepo,
    WeeklyReportRepo,
)
from quran_wird.domain.weekly import (
    WeekMember,
    WeekTask,
    summarise,
    week_bounds,
    weeks_to_finish,
)
from quran_wird.handlers.stats import weekly_preview
from quran_wird.jobs.weekly_report import send_weekly_report

CHAT = -100123

# A Saturday-to-Friday week, the default.
SATURDAY = dt.date(2026, 8, 29)
FRIDAY = dt.date(2026, 9, 4)


def task(day_offset: int, *done: int, pages: int = 2, repeat: bool = False) -> WeekTask:
    return WeekTask(
        task_date=SATURDAY + dt.timedelta(days=day_offset),
        page_count=pages,
        done_user_ids=frozenset(done),
        is_repeat=repeat,
    )


def member(user_id: int, name: str, joined_offset: int = 0) -> WeekMember:
    return WeekMember(
        user_id=user_id,
        display_name=name,
        joined_on=SATURDAY + dt.timedelta(days=joined_offset),
    )


# ------------------------------------------------------------- week boundaries


class TestWeekBounds:
    def test_a_saturday_week_runs_to_friday(self):
        # The default: week_start_weekday 5 is Saturday in date.weekday() terms.
        assert week_bounds(FRIDAY, 5) == (SATURDAY, FRIDAY)

    def test_the_first_day_of_the_week_starts_it(self):
        assert week_bounds(SATURDAY, 5) == (SATURDAY, FRIDAY)

    def test_a_midweek_day_finds_the_same_week(self):
        wednesday = SATURDAY + dt.timedelta(days=4)
        assert week_bounds(wednesday, 5) == (SATURDAY, FRIDAY)

    def test_a_monday_week_is_supported_too(self):
        monday = dt.date(2026, 8, 31)
        assert week_bounds(monday, 0) == (monday, monday + dt.timedelta(days=6))
        assert week_bounds(monday + dt.timedelta(days=6), 0)[0] == monday


class TestPace:
    def test_weeks_left_rounds_up(self):
        assert weeks_to_finish(current_page=1, pages_this_week=14) == 44  # 604 / 14
        assert weeks_to_finish(current_page=591, pages_this_week=14) == 1

    def test_a_stalled_week_gives_no_estimate(self):
        # Dividing by a week where nothing was read would promise eternity.
        assert weeks_to_finish(current_page=100, pages_this_week=0) is None


# ------------------------------------------------------------ the perfect week


class TestPerfectWeek:
    def test_reading_every_day_earns_the_board(self):
        tasks = [task(d, 1, 2) for d in range(7)]
        summary = summarise(tasks, [member(1, "أحمد"), member(2, "محمد")])
        assert [m.member.user_id for m in summary.perfect] == [1, 2]
        assert summary.everyone_perfect is True

    def test_one_missed_day_loses_it(self):
        tasks = [task(d, 1) for d in range(6)] + [task(6)]
        summary = summarise(tasks, [member(1, "أحمد")])
        assert summary.perfect == []
        assert summary.members[0].done == 6
        assert summary.members[0].eligible == 7

    def test_a_midweek_joiner_is_judged_only_from_their_first_day(self):
        # Joining on Wednesday must not be counted as missing Saturday...
        tasks = [task(d) for d in range(4)] + [task(d, 9) for d in range(4, 7)]
        summary = summarise(tasks, [member(9, "خالد", joined_offset=4)])
        entry = summary.members[0]
        assert (entry.done, entry.eligible) == (3, 3)
        assert entry.perfect is True

    def test_a_midweek_joiner_who_misses_is_still_marked(self):
        # ...but they are answerable for the days after they joined.
        tasks = [task(d) for d in range(4)] + [task(4, 9), task(5), task(6, 9)]
        summary = summarise(tasks, [member(9, "خالد", joined_offset=4)])
        assert summary.members[0].perfect is False

    def test_someone_who_joined_after_the_week_is_not_on_the_board(self):
        # No eligible days means nothing earned, rather than a free badge.
        tasks = [task(d, 1) for d in range(7)]
        summary = summarise(tasks, [member(1, "أحمد"), member(5, "جديد", joined_offset=10)])
        assert [m.member.user_id for m in summary.perfect] == [1]

    def test_a_repeated_day_still_has_to_be_read(self):
        # close_day counts a missed day for a repeat, so the board must agree —
        # otherwise the streak and the board would tell different stories.
        tasks = [task(0, 1), task(1, repeat=True)]
        summary = summarise(tasks, [member(1, "أحمد")])
        assert summary.perfect == []

    def test_repeated_pages_are_not_counted_twice(self):
        # Re-reading pages 1-2 does not move the khatmah, so the pages figure
        # must not claim it did.
        tasks = [task(0, 1, pages=2), task(1, 1, pages=2, repeat=True)]
        summary = summarise(tasks, [member(1, "أحمد")])
        assert summary.pages_read == 2
        assert summary.active_days == 2

    def test_the_group_rate_counts_only_eligible_days(self):
        tasks = [task(d, 1) for d in range(7)]
        summary = summarise(tasks, [member(1, "أحمد"), member(2, "محمد", joined_offset=5)])
        # 7 of 7 for the first, 0 of 2 for the second.
        assert (summary.completions, summary.possible_completions) == (7, 9)
        assert round(summary.completion_rate) == 78

    def test_a_week_with_no_members_is_empty_not_perfect(self):
        summary = summarise([task(0)], [])
        assert summary.everyone_perfect is False
        assert summary.completion_rate == 0.0

    def test_a_week_with_no_wird_is_not_a_perfect_week(self):
        # A group that paused for a week must not be congratulated for it.
        summary = summarise([], [member(1, "أحمد"), member(2, "محمد")])
        assert summary.everyone_perfect is False
        assert summary.perfect == []


# --------------------------------------------------------------- the job


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    g.active_weekdays = list(range(7))
    for page in range(1, 20):
        session.add(
            PageIndex(
                page_no=page,
                juz=1,
                hizb=1,
                first_surah=1,
                first_ayah=1,
                last_surah=2,
                last_ayah=5,
                surah_names="البقرة",
                ayah_count=5,
            )
        )
    await session.flush()
    return g


async def seed_week(session, *, done_by: dict[int, list[int]] | None = None) -> None:
    """One task a day for the week, with `done_by` mapping a day index to readers."""
    tasks = TaskRepo(session)
    done_by = done_by or {}
    for offset in range(7):
        row = await tasks.create(
            CHAT,
            task_date=SATURDAY + dt.timedelta(days=offset),
            page_start=1 + offset * 2,
            page_end=2 + offset * 2,
        )
        for uid in done_by.get(offset, []):
            await tasks.mark_done(row.id, uid, was_subscriber=True)


async def subscribe(session, *names: str, joined: dt.datetime | None = None) -> list[int]:
    repo = SubscriberRepo(session)
    ids = []
    for index, name in enumerate(names, start=1):
        sub, _ = await repo.subscribe(CHAT, index, display_name=name)
        sub.joined_at = joined or dt.datetime(2026, 8, 1)
        ids.append(index)
    await session.flush()
    return ids


class TestWeeklyReportJob:
    async def test_the_board_names_the_perfect_members(self, session, deps, group):
        await subscribe(session, "أحمد", "محمد")
        await seed_week(session, done_by={d: [1] for d in range(7)})

        bot = FakeBot()
        assert await send_weekly_report(bot, deps, CHAT, on=FRIDAY) is True

        text = bot.messages[0].text
        assert "لوحة شرف الأسبوع" in text
        assert "أحمد" in text
        assert "٧/٧" in text

    async def test_it_is_sent_once_a_week(self, session, deps, group):
        await subscribe(session, "أحمد")
        await seed_week(session)

        assert await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY) is True
        bot = FakeBot()
        # A restart on a Friday evening must not post a second board.
        assert await send_weekly_report(bot, deps, CHAT, on=FRIDAY) is False
        assert bot.messages == []

    async def test_the_perfect_week_badge_is_awarded_once(self, session, deps, group):
        uids = await subscribe(session, "أحمد")
        await seed_week(session, done_by={d: uids for d in range(7)})

        await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY)

        badges = BadgeRepo(session)
        assert await badges.count(CHAT, 1, BadgeKind.PERFECT_WEEK) == 1
        # The same week cannot be claimed again, so neither can its badge.
        await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY)
        assert await badges.count(CHAT, 1, BadgeKind.PERFECT_WEEK) == 1

    async def test_the_report_records_what_it_said(self, session, deps, group):
        await subscribe(session, "أحمد")
        await seed_week(session, done_by={d: [1] for d in range(7)})

        await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY)

        report = await WeeklyReportRepo(session).get(CHAT, SATURDAY)
        assert report.week_end == FRIDAY
        assert report.pages_read == 14
        assert report.perfect_user_ids == [1]
        assert report.message_id is not None

    async def test_nobody_perfect_still_sends_an_encouraging_board(self, session, deps, group):
        await subscribe(session, "أحمد")
        await seed_week(session)

        bot = FakeBot()
        await send_weekly_report(bot, deps, CHAT, on=FRIDAY)
        assert "لم يكتمل لأحدٍ الأسبوع" in bot.messages[0].text

    async def test_a_disabled_report_is_not_sent(self, session, deps, group):
        group.weekly_report_enabled = False
        await seed_week(session)
        assert await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY) is False

    async def test_a_paused_group_is_not_reported_on(self, session, deps, group):
        group.is_active = False
        await seed_week(session)
        assert await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY) is False

    async def test_a_week_with_no_wird_still_reports(self, session, deps, group):
        # A group that paused for a week gets a quiet board, not silence, not a
        # crash — and above all not a celebration of a week nobody read.
        await subscribe(session, "أحمد")
        bot = FakeBot()
        assert await send_weekly_report(bot, deps, CHAT, on=FRIDAY) is True

        text = bot.messages[0].text
        assert "لوحة شرف الأسبوع" in text
        assert "الأسبوع كامل للجميع" not in text
        assert "لم يكتمل لأحدٍ الأسبوع" in text


class TestWeekPreview:
    async def test_the_preview_does_not_claim_the_week(self, session, deps, group):
        await subscribe(session, "أحمد")
        await seed_week(session, done_by={d: [1] for d in range(7)})

        preview = await weekly_preview(deps, CHAT, on=FRIDAY)

        assert "لوحة شرف الأسبوع" in preview
        assert await WeeklyReportRepo(session).get(CHAT, SATURDAY) is None
        # And the real report still goes out on Friday evening.
        assert await send_weekly_report(FakeBot(), deps, CHAT, on=FRIDAY) is True

    async def test_no_badges_are_handed_out_by_a_preview(self, session, deps, group):
        await subscribe(session, "أحمد")
        await seed_week(session, done_by={d: [1] for d in range(7)})

        await weekly_preview(deps, CHAT, on=FRIDAY)
        assert await BadgeRepo(session).count(CHAT, 1, BadgeKind.PERFECT_WEEK) == 0

    async def test_an_unknown_group_previews_nothing(self, deps):
        assert await weekly_preview(deps, -999) is None


class TestTopStreakLine:
    async def test_the_longest_streak_is_named(self, session, deps, group):
        await subscribe(session, "أحمد", "محمد")
        await seed_week(session, done_by={d: [1, 2] for d in range(7)})
        stats = StatsRepo(session)
        (await stats.get_or_create(CHAT, 1)).current_streak = 23

        bot = FakeBot()
        await send_weekly_report(bot, deps, CHAT, on=FRIDAY)
        assert "٢٣ يومًا" in bot.messages[0].text

    async def test_a_streak_of_one_is_not_announced(self, session, deps, group):
        await subscribe(session, "أحمد")
        await seed_week(session)
        (await StatsRepo(session).get_or_create(CHAT, 1)).current_streak = 1

        bot = FakeBot()
        await send_weekly_report(bot, deps, CHAT, on=FRIDAY)
        assert "أطول سلسلة متتالية" not in bot.messages[0].text
