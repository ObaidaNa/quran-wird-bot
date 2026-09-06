"""The member-facing reports: /me, /progress, and /top."""

from __future__ import annotations

import datetime as dt

import pytest

from quran_wird.db.models import BadgeKind
from quran_wird.db.repo import BadgeRepo, GroupRepo, StatsRepo, SubscriberRepo, WeeklyReportRepo
from quran_wird.handlers.stats import group_progress, leaderboard, member_report

CHAT = -100123
TODAY = dt.date(2026, 9, 6)


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    await session.flush()
    return g


async def subscribe(session, *names: str) -> list[int]:
    repo = SubscriberRepo(session)
    ids = []
    for index, name in enumerate(names, start=1):
        await repo.subscribe(CHAT, index, display_name=name)
        ids.append(index)
    return ids


async def stats_for(session, user_id: int, **fields):
    stats = await StatsRepo(session).get_or_create(CHAT, user_id)
    for key, value in fields.items():
        setattr(stats, key, value)
    await session.flush()
    return stats


class TestMe:
    async def test_a_members_own_record(self, session, deps, group):
        await subscribe(session, "أحمد")
        await stats_for(
            session, 1, total_done=30, current_streak=12, best_streak=20, last_done_date=TODAY
        )

        text = await member_report(deps, CHAT, 1, display_name="أحمد")

        assert "١٢" in text  # the current streak
        assert "٣٠" in text  # days completed
        assert "٢٠" in text  # best streak

    async def test_the_best_streak_is_hidden_when_it_is_the_current_one(self, session, deps, group):
        # Saying "your best is 5" to someone on a run of 5 is noise.
        await subscribe(session, "أحمد")
        await stats_for(session, 1, current_streak=5, best_streak=5, total_done=5)
        assert "أطول سلسلة لك" not in await member_report(deps, CHAT, 1, display_name="أحمد")

    async def test_a_broken_streak_is_said_kindly(self, session, deps, group):
        await subscribe(session, "أحمد")
        await stats_for(session, 1, current_streak=0, total_done=4, total_missed=2)
        text = await member_report(deps, CHAT, 1, display_name="أحمد")
        assert "لا سلسلة جارية" in text

    async def test_perfect_weeks_are_counted(self, session, deps, group):
        await subscribe(session, "أحمد")
        await stats_for(session, 1, total_done=14)
        badges = BadgeRepo(session)
        await badges.award(CHAT, 1, BadgeKind.PERFECT_WEEK, ref="2026-08-29")
        await badges.award(CHAT, 1, BadgeKind.PERFECT_WEEK, ref="2026-09-05")

        assert "٢" in await member_report(deps, CHAT, 1, display_name="أحمد")

    async def test_a_stranger_is_invited_rather_than_shown_zeros(self, session, deps, group):
        text = await member_report(deps, CHAT, 99, display_name="زائر")
        assert "/join" in text

    async def test_someone_who_left_keeps_their_record(self, session, deps, group):
        await subscribe(session, "أحمد")
        await stats_for(session, 1, total_done=30, current_streak=3)
        await SubscriberRepo(session).unsubscribe(CHAT, 1)

        text = await member_report(deps, CHAT, 1, display_name="أحمد")
        assert "٣٠" in text
        # ...and is told how to come back.
        assert "/join" in text


class TestProgress:
    async def test_it_shows_the_page_and_the_bar(self, session, deps, group):
        group.current_page = 121
        text = await group_progress(deps, CHAT)
        assert "١٢١" in text and "٦٠٤" in text
        assert "▰" in text and "٪" in text

    async def test_the_estimate_uses_the_weeks_actually_read(self, session, deps, group):
        group.current_page = 121
        reports = WeeklyReportRepo(session)
        for offset, pages in ((0, 14), (7, 14)):
            await reports.claim(
                CHAT,
                week_start=dt.date(2026, 8, 15) + dt.timedelta(days=offset),
                week_end=dt.date(2026, 8, 21) + dt.timedelta(days=offset),
                pages_read=pages,
            )

        # 484 pages left at 14 a week is 35 weeks.
        assert "٣٥" in await group_progress(deps, CHAT)

    async def test_a_new_group_falls_back_to_its_configured_pace(self, session, deps, group):
        # No week behind it yet, so the estimate comes from the settings.
        assert "أسبوعًا" in await group_progress(deps, CHAT)

    async def test_an_unknown_group_has_no_progress(self, deps):
        assert await group_progress(deps, -999) is None


class TestTop:
    async def test_it_ranks_by_streak_then_by_total(self, session, deps, group):
        await subscribe(session, "أحمد", "محمد", "عمر")
        await stats_for(session, 1, current_streak=3, total_done=10)
        await stats_for(session, 2, current_streak=9, total_done=9)
        await stats_for(session, 3, current_streak=3, total_done=40)

        text = await leaderboard(deps, CHAT)
        order = [text.index(name) for name in ("محمد", "عمر", "أحمد")]
        assert order == sorted(order)

    async def test_it_names_nobody_who_left(self, session, deps, group):
        await subscribe(session, "أحمد", "محمد")
        await stats_for(session, 1, current_streak=5, total_done=5)
        await stats_for(session, 2, current_streak=9, total_done=9)
        await SubscriberRepo(session).unsubscribe(CHAT, 2)

        text = await leaderboard(deps, CHAT)
        assert "أحمد" in text and "محمد" not in text

    async def test_an_empty_board_says_so(self, deps, group):
        assert "لا إحصاءات بعد" in await leaderboard(deps, CHAT)

    async def test_names_are_plain_text_never_mentions(self, session, deps, group):
        # The board is a celebration, not a notification: mentions belong only
        # in the reminders aimed at latecomers.
        await subscribe(session, "أحمد")
        await stats_for(session, 1, current_streak=5, total_done=5)
        assert "tg://user" not in await leaderboard(deps, CHAT)
