"""The whole daily cycle, and the first-run path a real group takes.

Everything else tests one piece. This walks the bot through the days as a group
lives them: added → subscribes → wird → button → reminder → close → tomorrow,
including the first minutes after being added, which is the one path every group
takes and the easiest to leave unwired.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot
from telegram import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberUpdated,
    Update,
    User,
)

from quran_wird.db.models import PageIndex
from quran_wird.db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.deps import DEPS_KEY
from quran_wird.handlers.lifecycle import on_my_chat_member
from quran_wird.handlers.mark_done import record_completion
from quran_wird.jobs.close_day import close_day
from quran_wird.jobs.remind import send_reminder
from quran_wird.jobs.scheduler import CLOSE_JOB, SEND_JOB
from quran_wird.jobs.send_daily import send_wird
from quran_wird.jobs.weekly_report import WEEKLY_JOB

CHAT = -1001234567890
TODAY = dt.date.today()
BOT = User(id=1234567, first_name="بوت الورد", is_bot=True)
ADMIN = User(id=555, first_name="مشرف", is_bot=False)


# --------------------------------------------------------------- stub wiring


class StubJob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed = False

    def schedule_removal(self) -> None:
        self.removed = True


class StubQueue:
    """Records what was scheduled, and honours removals by name."""

    def __init__(self) -> None:
        self.scheduled: dict[str, dict] = {}

    def jobs(self, pattern=None):
        import re

        found = [StubJob(name) for name in self.scheduled if re.search(pattern or "", name)]
        for job in found:
            self.scheduled.pop(job.name, None)
        return found

    def run_daily(self, callback, time, days=None, chat_id=None, name=None, data=None):
        self.scheduled[name] = {"time": time, "days": days, "chat_id": chat_id}

    def run_once(self, callback, when, chat_id=None, data=None, name=None):
        self.scheduled[name] = {"when": when, "chat_id": chat_id, "data": data}

    def names(self, prefix: str) -> list[str]:
        return sorted(n for n in self.scheduled if n.startswith(prefix))


class StubApp:
    def __init__(self, deps, queue) -> None:
        self.bot_data = {DEPS_KEY: deps}
        self.job_queue = queue


class StubContext:
    def __init__(self, bot, deps, queue) -> None:
        self.bot = bot
        self.bot_data = {DEPS_KEY: deps}
        self.job_queue = queue
        self.application = StubApp(deps, queue)


# ------------------------------------------------------------------ fixtures


@pytest.fixture
async def index(session):
    for page in range(1, 12):
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


def group_chat() -> Chat:
    return Chat(id=CHAT, type=Chat.SUPERGROUP, title="مجموعة الورد")


def membership_update(chat: Chat, *, joining: bool):
    """The my_chat_member update Telegram sends when the bot is added or removed."""
    left = ChatMemberLeft(user=BOT)
    admin = ChatMemberAdministrator(
        user=BOT,
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=False,
        can_restrict_members=True,
        can_promote_members=False,
        can_change_info=True,
        can_invite_users=True,
        can_post_messages=True,
        can_edit_messages=True,
        can_pin_messages=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
    )
    return ChatMemberUpdated(
        chat=chat,
        from_user=ADMIN,
        date=dt.datetime.now(dt.UTC),
        old_chat_member=left if joining else admin,
        new_chat_member=admin if joining else left,
    )


# ------------------------------------------------------- being added to a group


class TestFirstRun:
    async def test_being_added_registers_and_schedules_the_group(self, session, deps, index):
        """The single most important path: a group adds the bot.

        Jobs live in memory and are otherwise built only at startup, so if this
        does not schedule them the group receives nothing at all until the
        process happens to restart.
        """
        queue = StubQueue()
        bot = FakeBot()
        chat = group_chat()
        update = Update(update_id=1, my_chat_member=membership_update(chat, joining=True))

        await on_my_chat_member(update, StubContext(bot, deps, queue))

        group = await GroupRepo(session).get(CHAT)
        assert group is not None and group.is_active
        assert queue.names(f"{SEND_JOB}:") == [f"{SEND_JOB}:{CHAT}"]
        assert queue.names(f"{CLOSE_JOB}:") == [f"{CLOSE_JOB}:{CHAT}"]
        assert queue.names(f"{WEEKLY_JOB}:") == [f"{WEEKLY_JOB}:{CHAT}"]
        assert "أنا بوت" in bot.messages[0].text

    async def test_the_first_wird_arrives_at_once(self, session, deps, index):
        """A group that adds the bot at ten in the morning must not wait until
        five the next morning to see anything happen."""
        queue = StubQueue()
        bot = FakeBot()
        update = Update(update_id=1, my_chat_member=membership_update(group_chat(), joining=True))

        await on_my_chat_member(update, StubContext(bot, deps, queue))

        task = await TaskRepo(session).get_open(CHAT)
        assert task is not None and (task.page_start, task.page_end) == (1, 2)
        assert len(bot.albums[0]) == 2
        # Welcome first, then the wird beneath it.
        assert "أنا بوت" in bot.messages[0].text
        assert "ورد اليوم" in bot.messages[1].text
        # And that wird's reminders are queued, exactly as the daily job would.
        assert queue.names("remind:")

    async def test_the_first_wird_is_pinned(self, session, deps, index):
        bot = FakeBot()
        await on_my_chat_member(
            Update(update_id=1, my_chat_member=membership_update(group_chat(), joining=True)),
            StubContext(bot, deps, StubQueue()),
        )
        task = await TaskRepo(session).get_open(CHAT)
        assert bot.pinned == [task.message_id]

    async def test_re_adding_the_same_day_does_not_post_twice(self, session, deps, index):
        queue = StubQueue()
        chat = group_chat()
        context = StubContext(FakeBot(), deps, queue)
        await on_my_chat_member(
            Update(update_id=1, my_chat_member=membership_update(chat, joining=True)), context
        )
        await on_my_chat_member(
            Update(update_id=2, my_chat_member=membership_update(chat, joining=False)), context
        )

        second = FakeBot()
        await on_my_chat_member(
            Update(update_id=3, my_chat_member=membership_update(chat, joining=True)),
            StubContext(second, deps, queue),
        )

        # The welcome is repeated; the wird is not.
        assert len(second.albums) == 0
        assert len(await TaskRepo(session).list_between(CHAT, TODAY, TODAY)) == 1

    async def test_missing_page_images_are_reported_not_swallowed(
        self, session, deps, index, pages_dir
    ):
        for image in pages_dir.glob("*.png"):
            image.unlink()

        bot = FakeBot()
        await on_my_chat_member(
            Update(update_id=1, my_chat_member=membership_update(group_chat(), joining=True)),
            StubContext(bot, deps, StubQueue()),
        )

        # The group is told what its operator has to do, rather than left silent.
        assert "build_pages" in bot.messages[-1].text

    async def test_being_removed_drops_the_jobs(self, session, deps, index):
        queue = StubQueue()
        chat = group_chat()
        context = StubContext(FakeBot(), deps, queue)

        await on_my_chat_member(
            Update(update_id=1, my_chat_member=membership_update(chat, joining=True)), context
        )
        await on_my_chat_member(
            Update(update_id=2, my_chat_member=membership_update(chat, joining=False)), context
        )

        # Otherwise the jobs keep firing into a chat the bot is not in.
        assert queue.scheduled == {}
        assert (await GroupRepo(session).get(CHAT)).is_active is False

    async def test_re_adding_resumes_the_khatmah(self, session, deps, index):
        queue = StubQueue()
        chat = group_chat()
        context = StubContext(FakeBot(), deps, queue)
        await on_my_chat_member(
            Update(update_id=1, my_chat_member=membership_update(chat, joining=True)), context
        )
        group = await GroupRepo(session).get(CHAT)
        group.current_page = 350
        await session.commit()

        await on_my_chat_member(
            Update(update_id=2, my_chat_member=membership_update(chat, joining=False)), context
        )
        await on_my_chat_member(
            Update(update_id=3, my_chat_member=membership_update(chat, joining=True)), context
        )

        after = await GroupRepo(session).get(CHAT)
        assert after.current_page == 350
        assert after.is_active is True
        assert queue.names(f"{SEND_JOB}:") == [f"{SEND_JOB}:{CHAT}"]


# --------------------------------------------------------------- a whole week


async def pretend_it_is_tomorrow(session, task_id: int) -> None:
    """Age a task by a day, so the next send_wird is a new day's send.

    send_wird refuses a second wird for the same task_date — that guard is what
    stops an admin double-posting — so a test spanning days has to move the
    calendar rather than the clock.
    """
    task = await TaskRepo(session).get(task_id)
    task.task_date -= dt.timedelta(days=1)
    task.sent_at -= dt.timedelta(days=1)
    await session.flush()


class TestDailyCycle:
    async def _group(self, session):
        group, _ = await GroupRepo(session).get_or_create(CHAT)
        group.active_weekdays = list(range(7))
        await session.flush()
        return group

    async def test_send_read_close_advance(self, session, deps, index):
        """One full day, then the next one starting where it left off."""
        group = await self._group(session)
        subs = SubscriberRepo(session)
        await subs.subscribe(CHAT, 1, display_name="أحمد")
        await subs.subscribe(CHAT, 2, display_name="محمد")

        # Morning: the wird goes out with its images and its buttons.
        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)
        assert len(bot.albums[0]) == 2
        assert bot.messages[0].reply_markup is not None

        # One member presses the button; the message is rewritten with the list.
        text, newly = await record_completion(deps, bot, CHAT, 1, display_name="أحمد")
        assert newly is True
        assert "أحمد" in bot.edits[-1]["text"]

        # Afternoon: only the member who has not read is reminded.
        assert await send_reminder(bot, deps, CHAT, task_id, 1) == 1
        reminder = bot.sent_texts[-1]
        assert "tg://user?id=2" in reminder and "tg://user?id=1" not in reminder

        # Night: the day closes, the pages move on, the stats are written.
        await close_day(bot, deps, CHAT)
        assert group.current_page == 3
        assert (await StatsRepo(session).get(CHAT, 1)).current_streak == 1
        assert (await StatsRepo(session).get(CHAT, 2)).consecutive_missed == 1

        # Tomorrow starts where today ended, and is not marked a repeat.
        await pretend_it_is_tomorrow(session, task_id)
        tomorrow = FakeBot()
        second = await send_wird(tomorrow, deps, CHAT)
        task = await TaskRepo(session).get(second)
        assert (task.page_start, task.page_end) == (3, 4)
        assert task.is_repeat_of is None

    async def test_the_wird_is_pinned_for_the_day_then_released(self, session, deps, index):
        # A pinned wird sits at the top of the group all day; leaving it pinned
        # would fill the pin list with every day the bot has run.
        await self._group(session)
        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)
        task = await TaskRepo(session).get(task_id)

        assert bot.pinned == [task.message_id]
        assert bot.unpinned == []

        await close_day(bot, deps, CHAT)
        assert bot.unpinned == [task.message_id]

    async def test_pinning_can_be_switched_off(self, session, deps, index):
        group = await self._group(session)
        group.pin_wird = False
        await session.flush()

        bot = FakeBot()
        await send_wird(bot, deps, CHAT)
        await close_day(bot, deps, CHAT)

        assert bot.pinned == [] and bot.unpinned == []

    async def test_a_bot_without_pin_rights_still_sends_the_wird(self, session, deps, index):
        # Being added without admin rights is common; the wird matters, the pin
        # does not.
        from telegram.error import BadRequest

        class NoRights(FakeBot):
            async def pin_chat_message(self, *a, **kw):
                raise BadRequest("not enough rights to pin a message")

        await self._group(session)
        bot = NoRights()
        assert await send_wird(bot, deps, CHAT) is not None
        assert len(bot.messages) == 1

    async def test_a_day_nobody_reads_repeats_tomorrow(self, session, deps, index):
        group = await self._group(session)
        await SubscriberRepo(session).subscribe(CHAT, 1, display_name="أحمد")

        first = await send_wird(FakeBot(), deps, CHAT)
        await close_day(FakeBot(), deps, CHAT)
        assert group.current_page == 1  # nobody read, so nothing moved

        await pretend_it_is_tomorrow(session, first)
        bot = FakeBot()
        second = await send_wird(bot, deps, CHAT)
        task = await TaskRepo(session).get(second)
        assert (task.page_start, task.page_end) == (1, 2)
        assert task.is_repeat_of == first
        assert "نُعيد ورد الأمس" in bot.messages[0].text

    async def test_the_cycle_is_idempotent_under_restarts(self, session, deps, index):
        """Every step replayed twice, as a crash-and-restart would replay it."""
        await self._group(session)
        await SubscriberRepo(session).subscribe(CHAT, 1, display_name="أحمد")

        bot = FakeBot()
        task_id = await send_wird(bot, deps, CHAT)
        assert await send_wird(bot, deps, CHAT) is None  # no second wird today

        await record_completion(deps, bot, CHAT, 1, display_name="أحمد")
        _, again = await record_completion(deps, bot, CHAT, 1, display_name="أحمد")
        assert again is False  # no double completion

        assert await send_reminder(bot, deps, CHAT, task_id, 1) == 0  # everyone finished
        assert await send_reminder(bot, deps, CHAT, task_id, 1) == 0

        assert await close_day(bot, deps, CHAT) == task_id
        assert await close_day(bot, deps, CHAT) is None  # no second close

        stats = await StatsRepo(session).get(CHAT, 1)
        assert (stats.total_done, stats.total_missed) == (1, 0)

    async def test_a_khatmah_from_page_603_rolls_over(self, session, deps, index):
        group = await self._group(session)
        group.current_page = 603
        await SubscriberRepo(session).subscribe(CHAT, 1, display_name="أحمد")
        await session.flush()

        # Page images only exist for 1-11 in the fixture, so send by hand.
        task = await TaskRepo(session).create(
            CHAT, task_date=dt.date.today(), page_start=603, page_end=604
        )
        await TaskRepo(session).mark_done(task.id, 1, was_subscriber=True)

        bot = FakeBot()
        await close_day(bot, deps, CHAT)

        assert (group.khatmah_number, group.current_page) == (2, 1)
        assert "تمّت الختمة رقم ١" in bot.messages[1].text
        assert bot.messages[2].kwargs["parse_mode"] is None
