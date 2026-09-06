"""Surviving the things a running bot actually meets: being kicked, and restarts."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest
from fakes import FakeBot
from telegram.error import Forbidden, TimedOut

from quran_wird.db.models import PageIndex
from quran_wird.db.repo import GroupRepo, TaskRepo
from quran_wird.deps import DEPS_KEY
from quran_wird.jobs.backup import BACKUP_JOB, schedule_backup
from quran_wird.jobs.close_day import job_close_day
from quran_wird.jobs.send_daily import job_send_daily
from quran_wird.tg.failures import deactivate_if_forbidden

CHAT = -100123


@dataclass
class StubJob:
    chat_id: int
    data: object = None


class StubContext:
    """The two attributes the job entry points actually touch."""

    def __init__(self, bot, deps, chat_id: int = CHAT) -> None:
        self.bot = bot
        self.bot_data = {DEPS_KEY: deps}
        self.job = StubJob(chat_id)
        self.job_queue = None


class KickedBot(FakeBot):
    """Telegram's answer once the bot has been removed from a group."""

    async def send_media_group(self, *a, **kw):
        raise Forbidden("Forbidden: bot was kicked from the supergroup chat")

    async def send_photo(self, *a, **kw):
        raise Forbidden("Forbidden: bot was kicked from the supergroup chat")

    async def send_message(self, *a, **kw):
        raise Forbidden("Forbidden: bot was kicked from the supergroup chat")


class FlakyBot(FakeBot):
    async def send_media_group(self, *a, **kw):
        raise TimedOut()

    async def send_photo(self, *a, **kw):
        raise TimedOut()


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
    g.active_weekdays = list(range(7))
    for page in range(1, 6):
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
    # Committed, not just flushed: a job that fails rolls its own transaction
    # back, and an uncommitted group would vanish with it.
    await session.commit()
    return g


async def reread(session):
    """The group as the database has it.

    A job that fails rolls its own transaction back, which expires the objects
    the test is holding; reading them again is what the next job would do.
    """
    return await GroupRepo(session).get(CHAT)


class TestBeingKicked:
    async def test_a_kicked_bot_stops_trying(self, session, deps, group):
        # Otherwise the group is retried every morning for as long as the bot
        # runs, and its jobs stay scheduled forever.
        await job_send_daily(StubContext(KickedBot(), deps))
        assert (await reread(session)).is_active is False

    async def test_closing_the_day_gives_up_too(self, session, deps, group):
        # close_day catches its own send errors so a failed report cannot undo a
        # closed day — but Forbidden has to reach the job, or a kicked group
        # would keep closing days forever.
        await TaskRepo(session).create(CHAT, task_date=dt.date.today(), page_start=1, page_end=2)
        await session.commit()

        await job_close_day(StubContext(KickedBot(), deps))
        assert (await reread(session)).is_active is False

    async def test_a_timeout_is_not_a_reason_to_give_up(self, session, deps, group):
        # A network blip must not silence a group; tomorrow's send will work.
        await job_send_daily(StubContext(FlakyBot(), deps))
        assert (await reread(session)).is_active is True

    async def test_the_khatmah_survives_deactivation(self, session, deps, group):
        group.current_page = 5
        await session.commit()

        await job_send_daily(StubContext(KickedBot(), deps))

        after = await reread(session)
        # Re-adding the bot resumes from page 5 rather than starting over.
        assert after.is_active is False
        assert after.current_page == 5

    async def test_other_errors_are_left_alone(self, deps):
        assert await deactivate_if_forbidden(deps, CHAT, TimedOut()) is False


class TestBackupScheduling:
    def test_it_is_scheduled_once_for_the_whole_bot(self, deps):
        queue = StubQueue()
        assert schedule_backup(queue, deps.settings) is True
        assert [j["name"] for j in queue.daily] == [BACKUP_JOB]
        # Not one per group: there is only one database.
        assert queue.daily[0]["days"] is None

    def test_it_can_be_switched_off(self, deps):
        deps.settings.backup_enabled = False
        queue = StubQueue()
        assert schedule_backup(queue, deps.settings) is False
        assert queue.daily == []

    def test_rescheduling_does_not_double_it(self, deps):
        queue = StubQueue()
        schedule_backup(queue, deps.settings)
        schedule_backup(queue, deps.settings)
        assert all(j.removed for j in queue.handed_out)


class StubQueue:
    def __init__(self) -> None:
        self.daily: list[dict] = []
        self.handed_out: list[StubRemovable] = []

    def jobs(self, pattern=None):
        job = StubRemovable(pattern)
        self.handed_out.append(job)
        return [job]

    def run_daily(self, callback, time, days=None, chat_id=None, name=None, data=None):
        self.daily.append({"callback": callback, "time": time, "days": days, "name": name})


class StubRemovable:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed = False

    def schedule_removal(self) -> None:
        self.removed = True
