"""Closing the day: stats, advance rules, the khatmah rollover, and repeats."""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot

from quran_wird.db.models import AdvanceRule, PageIndex, TaskStatus
from quran_wird.db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.jobs.close_day import close_day, khatmah_days
from quran_wird.jobs.send_daily import send_wird
from quran_wird.messages.phrases import KHATMAH_DUA

CHAT = -100123
TODAY = dt.date.today()


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    g.active_weekdays = list(range(7))
    for page in range(1, 13):
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


async def subscribe(session, *names: str) -> list[int]:
    repo = SubscriberRepo(session)
    ids = []
    for index, name in enumerate(names, start=1):
        await repo.subscribe(CHAT, index, display_name=name)
        ids.append(index)
    return ids


async def open_task(session, *, pages=(1, 2), on: dt.date | None = None):
    return await TaskRepo(session).create(
        CHAT, task_date=on or TODAY, page_start=pages[0], page_end=pages[1]
    )


async def finish(session, task_id: int, *user_ids: int, subscriber: bool = True) -> None:
    for uid in user_ids:
        await TaskRepo(session).mark_done(task_id, uid, was_subscriber=subscriber)


# ------------------------------------------------------------------ advancing


class TestAdvancement:
    async def test_nobody_read_leaves_the_page_pointer_alone(self, session, deps, group):
        await subscribe(session, "أحمد")
        await open_task(session, pages=(1, 2))

        await close_day(FakeBot(), deps, CHAT)

        assert group.current_page == 1

    async def test_one_finisher_advances_under_the_default_rule(self, session, deps, group):
        uids = await subscribe(session, "أحمد", "محمد")
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, uids[0])

        await close_day(FakeBot(), deps, CHAT)

        assert group.advance_rule is AdvanceRule.ANYONE
        assert group.current_page == 3

    async def test_all_rule_waits_for_everyone(self, session, deps, group):
        group.advance_rule = AdvanceRule.ALL
        uids = await subscribe(session, "أحمد", "محمد")
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, uids[0])

        await close_day(FakeBot(), deps, CHAT)
        assert group.current_page == 1

    async def test_majority_rule_needs_more_than_half(self, session, deps, group):
        group.advance_rule = AdvanceRule.MAJORITY
        uids = await subscribe(session, "أحمد", "محمد", "عمر")
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, *uids[:2])

        await close_day(FakeBot(), deps, CHAT)
        assert group.current_page == 3

    async def test_always_rule_advances_with_nobody_reading(self, session, deps, group):
        group.advance_rule = AdvanceRule.ALWAYS
        await subscribe(session, "أحمد")
        await open_task(session, pages=(1, 2))

        await close_day(FakeBot(), deps, CHAT)
        assert group.current_page == 3

    async def test_a_guest_alone_does_not_advance_the_group(self, session, deps, group):
        # The locked rule reads "at least one *subscriber*": a passer-by pressing
        # the button must not move a khatmah the group shares.
        await subscribe(session, "أحمد")
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, 999, subscriber=False)

        await close_day(FakeBot(), deps, CHAT)
        assert group.current_page == 1


# --------------------------------------------------------------------- stats


class TestStats:
    async def test_finishers_keep_their_streak_and_missers_lose_it(self, session, deps, group):
        uids = await subscribe(session, "أحمد", "محمد")
        task = await open_task(session)
        await finish(session, task.id, uids[0])

        stats = StatsRepo(session)
        await stats.record_done(CHAT, uids[0], TODAY)  # as the button already did
        await stats.record_missed(CHAT, uids[1])
        (await stats.get(CHAT, uids[1])).current_streak = 5

        await close_day(FakeBot(), deps, CHAT)

        done = await stats.get(CHAT, uids[0])
        missed = await stats.get(CHAT, uids[1])
        assert (done.total_done, done.current_streak) == (1, 1)
        assert missed.current_streak == 0
        assert missed.consecutive_missed == 2

    async def test_closing_does_not_double_count_a_finisher(self, session, deps, group):
        uid = (await subscribe(session, "أحمد"))[0]
        task = await open_task(session)
        await finish(session, task.id, uid)
        await StatsRepo(session).record_done(CHAT, uid, TODAY)

        await close_day(FakeBot(), deps, CHAT)

        stats = await StatsRepo(session).get(CHAT, uid)
        assert (stats.total_done, stats.current_streak) == (1, 1)

    async def test_guests_are_never_counted_as_missing(self, session, deps, group):
        task = await open_task(session)
        await finish(session, task.id, 999, subscriber=False)

        await close_day(FakeBot(), deps, CHAT)

        assert await StatsRepo(session).get(CHAT, 999) is None

    async def test_closing_twice_changes_nothing(self, session, deps, group):
        uid = (await subscribe(session, "أحمد"))[0]
        await open_task(session)

        assert await close_day(FakeBot(), deps, CHAT) is not None
        bot = FakeBot()
        assert await close_day(bot, deps, CHAT) is None

        # The second close is a no-op: no second report, no second missed day.
        assert bot.messages == []
        assert (await StatsRepo(session).get(CHAT, uid)).total_missed == 1

    async def test_the_task_is_closed(self, session, deps, group):
        task = await open_task(session)
        await close_day(FakeBot(), deps, CHAT)
        assert (await TaskRepo(session).get(task.id)).status is TaskStatus.CLOSED


# ------------------------------------------------------------------- reports


class TestDailySummary:
    async def test_summary_names_the_finishers_and_the_rate(self, session, deps, group):
        uids = await subscribe(session, "أحمد", "محمد", "عمر", "خالد")
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, *uids[:3])

        bot = FakeBot()
        await close_day(bot, deps, CHAT)

        text = bot.messages[0].text
        assert "خُلاصة اليوم" in text
        assert "أنجز ٣ من ٤" in text and "٧٥٪" in text
        assert "أحمد" in text
        # Who fell behind is said in the reminders, never announced to the group.
        assert "خالد" not in text

    async def test_summary_announces_tomorrows_pages_when_advancing(self, session, deps, group):
        uid = (await subscribe(session, "أحمد"))[0]
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, uid)

        bot = FakeBot()
        await close_day(bot, deps, CHAT)
        assert "ورد الغد: الصفحات ٣ – ٤" in bot.messages[0].text

    async def test_summary_says_the_pages_repeat_when_nobody_read(self, session, deps, group):
        await subscribe(session, "أحمد")
        await open_task(session, pages=(1, 2))

        bot = FakeBot()
        await close_day(bot, deps, CHAT)
        assert "نُعيد صفحات اليوم غدًا" in bot.messages[0].text

    async def test_a_failed_report_still_leaves_the_day_closed(self, session, deps, group):
        from telegram.error import TelegramError

        class MuteBot(FakeBot):
            async def send_message(self, *a, **kw):
                raise TelegramError("chat not found")

        task = await open_task(session)
        assert await close_day(MuteBot(), deps, CHAT) == task.id
        assert (await TaskRepo(session).get(task.id)).status is TaskStatus.CLOSED


# ------------------------------------------------------------------- khatmah


class TestKhatmahRollover:
    async def _at_the_end(self, session, group):
        group.current_page = 603
        uid = (await subscribe(session, "أحمد"))[0]
        task = await open_task(session, pages=(603, 604))
        await finish(session, task.id, uid)
        return task

    async def test_finishing_the_mushaf_starts_a_new_khatmah(self, session, deps, group):
        await self._at_the_end(session, group)

        await close_day(FakeBot(), deps, CHAT)

        assert group.khatmah_number == 2
        assert group.current_page == 1
        assert group.khatmah_started_on == TODAY + dt.timedelta(days=1)

    async def test_the_dua_is_sent_unparsed_in_its_own_message(self, session, deps, group):
        await self._at_the_end(session, group)

        bot = FakeBot()
        await close_day(bot, deps, CHAT)

        summary, announcement, dua = bot.messages
        assert "تمّت الختمة" in summary.text
        assert "تمّت الختمة رقم ١" in announcement.text
        # HTML parsing would mangle the tashkeel and the hard line breaks.
        assert dua.text == KHATMAH_DUA
        assert dua.kwargs["parse_mode"] is None

    async def test_no_khatmah_message_on_an_ordinary_day(self, session, deps, group):
        uid = (await subscribe(session, "أحمد"))[0]
        task = await open_task(session, pages=(1, 2))
        await finish(session, task.id, uid)

        bot = FakeBot()
        await close_day(bot, deps, CHAT)
        assert len(bot.messages) == 1

    def test_khatmah_days_counts_both_ends(self, group):
        group.khatmah_started_on = dt.date(2026, 1, 1)
        assert khatmah_days(group, dt.date(2026, 1, 1)) == 1
        assert khatmah_days(group, dt.date(2026, 1, 10)) == 10

    def test_khatmah_days_falls_back_to_the_group_start(self, group):
        group.khatmah_started_on = None
        group.created_at = dt.datetime(2026, 1, 1, 5, 0)
        assert khatmah_days(group, dt.date(2026, 1, 5)) == 5


# ------------------------------------------------------- close then send again


class TestRepeatAfterClose:
    async def test_an_unread_wird_is_repeated_and_marked_the_next_day(self, session, deps, group):
        await subscribe(session, "أحمد")
        yesterday = await open_task(session, pages=(1, 2), on=TODAY - dt.timedelta(days=1))
        await close_day(FakeBot(), deps, CHAT)

        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)

        repeat = await TaskRepo(session).get(task_id)
        assert (repeat.page_start, repeat.page_end) == (1, 2)
        # close_day closes the task, so the open-task fallback cannot see it; the
        # repeat is recognised by the pages matching yesterday's instead.
        assert repeat.is_repeat_of == yesterday.id
        assert "نُعيد ورد الأمس — لم يُتمّه أحد" in bot.messages[0].text

    async def test_a_partial_repeat_does_not_claim_nobody_read(self, session, deps, group):
        group.advance_rule = AdvanceRule.ALL
        uids = await subscribe(session, "أحمد", "محمد")
        yesterday = await open_task(session, pages=(1, 2), on=TODAY - dt.timedelta(days=1))
        await finish(session, yesterday.id, uids[0])
        await close_day(FakeBot(), deps, CHAT)

        bot = FakeBot()
        await send_wird(bot, deps, CHAT)
        assert "لم يُتمّه الجميع بعد" in bot.messages[0].text

    async def test_advancing_leaves_the_next_wird_unmarked(self, session, deps, group):
        uid = (await subscribe(session, "أحمد"))[0]
        yesterday = await open_task(session, pages=(1, 2), on=TODAY - dt.timedelta(days=1))
        await finish(session, yesterday.id, uid)
        await close_day(FakeBot(), deps, CHAT)

        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)

        repeat = await TaskRepo(session).get(task_id)
        assert (repeat.page_start, repeat.page_end) == (3, 4)
        assert repeat.is_repeat_of is None
        assert "نُعيد ورد الأمس" not in bot.messages[0].text

    async def test_a_new_khatmah_is_not_a_repeat_of_the_last_wird(self, session, deps, group):
        # Page 1 follows page 604, and the ranges differ, so nothing marks it.
        group.current_page = 603
        uid = (await subscribe(session, "أحمد"))[0]
        yesterday = await open_task(session, pages=(603, 604), on=TODAY - dt.timedelta(days=1))
        await finish(session, yesterday.id, uid)
        await close_day(FakeBot(), deps, CHAT)

        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)

        task = await TaskRepo(session).get(task_id)
        assert (task.page_start, task.page_end) == (1, 2)
        assert task.is_repeat_of is None


# ------------------------------------------------------------------ scheduling


class StubJob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed = False

    def schedule_removal(self) -> None:
        self.removed = True


class StubQueue:
    """Just enough JobQueue to see what schedule_group registers and removes."""

    def __init__(self) -> None:
        self.daily: list[dict] = []
        self.handed_out: list[StubJob] = []

    def jobs(self, pattern=None):
        job = StubJob(pattern)
        self.handed_out.append(job)
        return [job]

    def run_daily(self, callback, time, days=None, chat_id=None, name=None):
        self.daily.append({"callback": callback, "time": time, "days": days, "name": name})


class TestScheduling:
    def _schedule(self, group) -> StubQueue:
        from quran_wird.jobs.scheduler import schedule_group

        queue = StubQueue()
        schedule_group(queue, group)
        return queue

    def test_the_close_job_runs_on_every_day_of_the_week(self, group):
        group.active_weekdays = [5, 6]  # Saturday and Sunday only
        job = next(j for j in self._schedule(group).daily if j["name"].startswith("close:"))

        # No `days=` filter on purpose: a wird sent on the group's last active
        # day still has to close that night, on a day the group has switched off.
        assert job["days"] is None
        assert (job["time"].hour, job["time"].minute) == (23, 59)

    def test_the_close_job_is_cleared_before_rescheduling(self, group):
        # Otherwise a settings change would leave the old close time running too.
        patterns = [j.name for j in self._schedule(group).handed_out if j.removed]
        assert f"^close:{CHAT}$" in patterns
