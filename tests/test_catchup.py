"""The catch-up plan end to end: enrollment, pieces, repair, and the tick."""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot
from telegram.error import Forbidden

from quran_wird.db.models import Ayah, PageIndex, Surah, utcnow
from quran_wird.db.repo import CatchupRepo, GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.domain import catchup as policy
from quran_wird.handlers.catchup import complete_pending, enroll_or_revive, set_paused, show_piece
from quran_wird.handlers.mark_done import record_completion
from quran_wird.jobs.catchup import run_tick
from quran_wird.jobs.close_day import close_day
from quran_wird.messages import ar

CHAT = -100123
USER = 42
TODAY = dt.date.today()
NOW = dt.datetime(2026, 10, 22, 10, 0)  # midday in the group's timezone


async def seed_mushaf(session, *, pages: int = 12, ayahs_per_page: int = 3) -> None:
    """A tiny index: page 1 is al-Fatihah, the rest are al-Baqarah, 3 ayahs a page."""
    session.add(
        Surah(
            number=1,
            name="سورة الفاتحة",
            short_name="الفاتحة",
            ayah_count=ayahs_per_page,
            first_page=1,
            last_page=1,
        )
    )
    session.add(
        Surah(
            number=2,
            name="سورة البقرة",
            short_name="البقرة",
            ayah_count=(pages - 1) * ayahs_per_page,
            first_page=2,
            last_page=pages,
        )
    )
    for page in range(1, pages + 1):
        if page == 1:
            first = (1, 1)
            last = (1, ayahs_per_page)
        else:
            base = (page - 2) * ayahs_per_page
            first = (2, base + 1)
            last = (2, base + ayahs_per_page)
        session.add(
            PageIndex(
                page_no=page,
                juz=1,
                hizb=1,
                first_surah=first[0],
                first_ayah=first[1],
                last_surah=last[0],
                last_ayah=last[1],
                surah_names="البقرة",
                ayah_count=ayahs_per_page,
            )
        )
        for number in range(first[1], last[1] + 1):
            session.add(
                Ayah(
                    surah=first[0],
                    number=number,
                    page_no=page,
                    juz=1,
                    hizb=1,
                    text=f"نص الآية {number}",
                )
            )
    await session.flush()


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    g.active_weekdays = list(range(7))
    await seed_mushaf(session)
    return g


async def subscribe(session, user_id: int = USER, *, days_ago: int = 10) -> None:
    sub, _ = await SubscriberRepo(session).subscribe(CHAT, user_id, display_name="أحمد")
    sub.joined_at = utcnow() - dt.timedelta(days=days_ago)
    await session.flush()


async def closed_task(session, pages: tuple[int, int], days_ago: int) -> object:
    task = await TaskRepo(session).create(
        CHAT,
        task_date=TODAY - dt.timedelta(days=days_ago),
        page_start=pages[0],
        page_end=pages[1],
    )
    await TaskRepo(session).close(task.id)
    await StatsRepo(session).record_missed(CHAT, USER, task_id=task.id)
    return task


async def enroll(deps, bot) -> object:
    await enroll_or_revive(deps, bot, CHAT, USER, display_name="أحمد")
    return await CatchupRepo(deps.sessions.session).get(CHAT, USER)


# --------------------------------------------------------------- enrollment


class TestEnrollment:
    async def test_debt_is_the_missed_closed_wirds_only(self, session, deps, group):
        await subscribe(session)
        read = await closed_task(session, (1, 2), days_ago=2)
        await TaskRepo(session).mark_done(read.id, USER, was_subscriber=True)
        await closed_task(session, (3, 4), days_ago=1)  # missed
        await closed_task(session, (5, 6), days_ago=0)  # missed today, closed

        bot = FakeBot()
        plan = await enroll(deps, bot)

        assert plan is not None
        assert plan.queue == [[3, 6]]
        assert plan.dose == policy.START_DOSE
        assert bot.photos and bot.photos[0]["chat_id"] == USER
        assert all(m.chat_id == USER for m in bot.messages)

    async def test_pages_missed_twice_are_owed_once(self, session, deps, group):
        """A repeated wird the member still skipped is one debt, not two."""
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=2)
        await closed_task(session, (3, 4), days_ago=1)  # the repeat

        plan = await enroll(deps, FakeBot())
        assert plan.queue == [[3, 4]]

    async def test_nothing_missed_means_no_plan(self, session, deps, group):
        await subscribe(session)
        bot = FakeBot()
        text = await enroll_or_revive(deps, bot, CHAT, USER, display_name="أحمد")
        assert text == ar.CATCHUP_NO_DEBT
        assert await CatchupRepo(session).get(CHAT, USER) is None

    async def test_non_subscribers_are_turned_away(self, session, deps, group):
        text = await enroll_or_revive(deps, FakeBot(), CHAT, USER, display_name="ضيف")
        assert text == ar.CATCHUP_NOT_SUBSCRIBED

    async def test_enrollment_sends_the_first_piece_immediately(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)

        bot = FakeBot()
        plan = await enroll(deps, bot)

        assert plan.pending_start is not None
        piece_messages = [m for m in bot.messages if "مقطع التدارك" in (m.text or "")]
        assert piece_messages


# ----------------------------------------------------------- finishing pieces


class TestCompletePending:
    async def test_the_cursor_advances_and_the_dose_grows(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        plan = await enroll(deps, FakeBot())
        dose_before = plan.dose
        plan.success_streak = policy.SUCCESSES_TO_GROW - 1

        toast = await complete_pending(deps, FakeBot(), CHAT, USER, display_name="أحمد")

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.pending_start is None
        assert plan.cursor > 1
        assert plan.dose == policy.dose_after_success(dose_before)
        assert toast  # a thank-you phrase came back

    async def test_a_second_press_on_the_same_piece_counts_nothing(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        await enroll(deps, FakeBot())

        await complete_pending(deps, FakeBot(), CHAT, USER, display_name="أحمد")
        again = await complete_pending(deps, FakeBot(), CHAT, USER, display_name="أحمد")
        assert again == ar.DONE_ALREADY

    async def test_finishing_a_missed_day_repairs_it(self, session, deps, group):
        """The missed day moves to the done column, with a late completion row."""
        await subscribe(session)
        task = await closed_task(session, (3, 3), days_ago=1)

        bot = FakeBot()
        plan = await enroll(deps, bot)
        assert plan.dose == 1

        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد")

        stats = await StatsRepo(session).get(CHAT, USER)
        assert stats.total_missed == 0
        assert stats.total_done == 1
        assert await TaskRepo(session).has_done(task.id, USER)
        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "caught_up"
        # The group heard about the return.
        assert any(m.chat_id == CHAT and "لحق بالمجموعة" in (m.text or "") for m in bot.messages)

    async def test_catching_up_announces_the_group_and_resets_dereliction(
        self, session, deps, group
    ):
        await subscribe(session)
        await closed_task(session, (3, 3), days_ago=1)
        stats = await StatsRepo(session).get(CHAT, USER)
        stats.consecutive_missed = 3

        bot = FakeBot()
        await enroll(deps, bot)
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد")

        stats = await StatsRepo(session).get(CHAT, USER)
        assert stats.consecutive_missed == 0
        announced = [m for m in bot.messages if m.chat_id == CHAT]
        assert announced


# ------------------------------------------------------- the group's own flow


class TestGroupFlow:
    async def test_close_day_adds_the_missed_wird_to_existing_plans(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 3), days_ago=2)
        await enroll(deps, FakeBot())

        # Today: the group reads pages 4-5 and our member does not.
        task = await TaskRepo(session).create(CHAT, task_date=TODAY, page_start=4, page_end=5)
        await close_day(FakeBot(), deps, CHAT)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.queue == [[3, 5]]
        assert task.id in plan.debt_tasks

    async def test_close_day_wakes_a_caught_up_plan_with_new_debt(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 3), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد")
        assert (await CatchupRepo(session).get(CHAT, USER)).state == "caught_up"

        await TaskRepo(session).create(CHAT, task_date=TODAY, page_start=4, page_end=4)
        await close_day(FakeBot(), deps, CHAT)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "active"
        assert plan.queue == [[4, 4]]
        # The cursor was reset onto the new debt, not left past it.
        assert plan.cursor <= 10

    async def test_pages_read_with_the_group_leave_the_debt(self, session, deps, group):
        """A repeated wird the member finally reads in the group is not owed."""
        await subscribe(session)
        await closed_task(session, (3, 3), days_ago=1)
        await enroll(deps, FakeBot())

        # The group repeats page 3 today, and the member presses the button.
        task = await TaskRepo(session).create(CHAT, task_date=TODAY, page_start=3, page_end=3)
        bot = FakeBot()
        await record_completion(deps, bot, CHAT, USER, display_name="أحمد", task_id=task.id)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.queue == []
        assert plan.state == "caught_up"
        assert any("لحق بالمجموعة" in (m.text or "") for m in bot.messages)


# ------------------------------------------------------------------- the tick


class TestTick:
    async def test_a_pending_piece_is_nudged_after_its_gap(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        plan.last_sent_at = NOW - dt.timedelta(hours=4)
        await session.flush()

        sent = await run_tick(bot, deps, now=NOW)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert sent == 1
        assert plan.ignored_streak == 1
        assert plan.dose == policy.dose_after_lapse(policy.START_DOSE)
        assert plan.tone_index == 1

    async def test_the_tick_is_idempotent_against_itself(self, session, deps, group):
        """A replayed tick finds the claim and sends nothing again."""
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        plan.last_sent_at = NOW - dt.timedelta(hours=4)
        await session.flush()

        await run_tick(bot, deps, now=NOW)
        again = await run_tick(bot, deps, now=NOW)
        assert again == 0

    async def test_seven_unanswered_nudges_pause_the_plan(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        plan.ignored_streak = policy.AUTO_PAUSE_AFTER
        await session.flush()

        await run_tick(bot, deps, now=NOW)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "paused"
        assert any("أمهلتُك" in (m.text or "") for m in bot.messages)

    async def test_a_paused_plan_is_probed_weekly_then_goes_dormant(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        plan.state = "paused"
        plan.paused_by_user = False
        await session.flush()

        for week in range(policy.DORMANT_AFTER_PROBES + 1):
            await run_tick(bot, deps, now=NOW + dt.timedelta(days=7 * (week + 1)))

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "dormant"
        # And dormancy is silent: one more tick sends nothing.
        sent = await run_tick(bot, deps, now=NOW + dt.timedelta(days=60))
        assert sent == 0

    async def test_a_blocked_member_blocks_the_plan_not_the_group(self, session, deps, group):
        class BlockingBot(FakeBot):
            async def send_message(self, chat_id, text, **kw):
                if chat_id == USER:
                    raise Forbidden("bot was blocked by the user")
                return await super().send_message(chat_id, text, **kw)

        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        plan.last_sent_at = NOW - dt.timedelta(hours=4)
        await session.flush()

        await run_tick(BlockingBot(), deps, now=NOW)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "blocked"
        assert (await GroupRepo(session).get(CHAT)).is_active


# ------------------------------------------------------------- pause / resume


class TestPauseResume:
    async def test_a_member_paused_plan_gets_no_probes(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        await enroll(deps, bot)
        await set_paused(deps, CHAT, USER, paused=True, by_user=True)

        sent = await run_tick(bot, deps, now=NOW + dt.timedelta(days=30))
        assert sent == 0
        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "paused"

    async def test_show_piece_revives_a_resting_plan(self, session, deps, group):
        await subscribe(session)
        await closed_task(session, (3, 4), days_ago=1)
        bot = FakeBot()
        plan = await enroll(deps, bot)
        await set_paused(deps, CHAT, USER, paused=True, by_user=True)

        await show_piece(deps, bot, CHAT, USER)

        plan = await CatchupRepo(session).get(CHAT, USER)
        assert plan.state == "active"
