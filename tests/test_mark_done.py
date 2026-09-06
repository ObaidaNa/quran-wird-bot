"""Marking the wird done: idempotency, live refresh, stats, and guests."""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot

from quran_wird.db.models import CompletionSource, PageIndex
from quran_wird.db.repo import GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.handlers.mark_done import record_completion
from quran_wird.messages import ar
from quran_wird.messages.phrases import ALL_DONE, DONE

CHAT = -100123
TODAY = dt.date.today()


@pytest.fixture
async def group(session):
    g, _ = await GroupRepo(session).get_or_create(CHAT)
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


async def subscribe(session, *user_ids):
    repo = SubscriberRepo(session)
    for uid in user_ids:
        await repo.subscribe(CHAT, uid, display_name=f"عضو {uid}")


class TestRecordCompletion:
    async def test_subscriber_marks_done(self, session, deps, task):
        await subscribe(session, 1)
        text, newly = await record_completion(deps, FakeBot(), CHAT, 1, display_name="أحمد")
        assert newly is True
        assert text in DONE
        assert await TaskRepo(session).has_done(task.id, 1)

    async def test_second_press_is_rejected(self, session, deps, task):
        await subscribe(session, 1)
        bot = FakeBot()
        await record_completion(deps, bot, CHAT, 1, display_name="أحمد")
        text, newly = await record_completion(deps, bot, CHAT, 1, display_name="أحمد")

        assert newly is False
        assert text == ar.DONE_ALREADY
        assert await TaskRepo(session).done_count(task.id) == 1

    async def test_streak_is_recorded_for_subscribers(self, session, deps, task):
        await subscribe(session, 1)
        await record_completion(deps, FakeBot(), CHAT, 1, display_name="أحمد")

        stats = await StatsRepo(session).get(CHAT, 1)
        assert stats.total_done == 1
        assert stats.current_streak == 1
        assert stats.last_done_date == TODAY

    async def test_display_name_is_refreshed(self, session, deps, task):
        await subscribe(session, 1)
        await record_completion(deps, FakeBot(), CHAT, 1, display_name="أحمد الحسن")
        sub = await SubscriberRepo(session).get(CHAT, 1)
        assert sub.display_name == "أحمد الحسن"

    async def test_no_open_wird(self, session, deps, group):
        text, newly = await record_completion(deps, FakeBot(), CHAT, 1, display_name="أحمد")
        assert newly is False
        assert text == ar.NO_OPEN_WIRD

    async def test_closed_wird_is_refused(self, session, deps, task):
        await TaskRepo(session).close(task.id)
        text, newly = await record_completion(
            deps, FakeBot(), CHAT, 1, display_name="أحمد", task_id=task.id
        )
        assert newly is False
        assert text == ar.WIRD_CLOSED

    async def test_task_from_another_group_is_refused(self, session, deps, task):
        # A crafted callback must not let someone mark another group's wird.
        text, newly = await record_completion(
            deps, FakeBot(), -999, 1, display_name="أحمد", task_id=task.id
        )
        assert newly is False
        assert text == ar.NO_OPEN_WIRD

    async def test_done_command_source_is_recorded(self, session, deps, task):
        await subscribe(session, 1)
        await record_completion(
            deps,
            FakeBot(),
            CHAT,
            1,
            display_name="أحمد",
            source=CompletionSource.COMMAND,
        )
        assert await TaskRepo(session).has_done(task.id, 1)


class TestMessageRefresh:
    async def test_message_is_edited_with_the_finisher(self, session, deps, task):
        await subscribe(session, 1, 2)
        await TaskRepo(session).set_messages(task.id, message_id=555, album_message_ids=[1])

        bot = FakeBot()
        await record_completion(deps, bot, CHAT, 1, display_name="أحمد")

        assert len(bot.edits) == 1
        edit = bot.edits[0]
        assert edit["message_id"] == 555
        assert "أنجز ١ من ٢" in edit["text"]

    async def test_no_edit_when_the_message_id_is_unknown(self, session, deps, task):
        await subscribe(session, 1)
        bot = FakeBot()
        await record_completion(deps, bot, CHAT, 1, display_name="أحمد")
        assert bot.edits == []

    async def test_not_modified_error_is_swallowed(self, session, deps, task):
        await subscribe(session, 1)
        await TaskRepo(session).set_messages(task.id, message_id=555, album_message_ids=None)

        bot = FakeBot(edit_error="Message is not modified")
        # A concurrent edit writing identical text must not fail the completion.
        text, newly = await record_completion(deps, bot, CHAT, 1, display_name="أحمد")
        assert newly is True
        assert text in DONE


class TestEveryoneFinished:
    async def test_celebration_when_all_subscribers_finish(self, session, deps, task):
        await subscribe(session, 1, 2)
        bot = FakeBot()

        await record_completion(deps, bot, CHAT, 1, display_name="عضو ١")
        assert bot.sent_texts == []  # still one pending

        await record_completion(deps, bot, CHAT, 2, display_name="عضو ٢")
        assert any(t in ALL_DONE for t in bot.sent_texts)

    async def test_no_celebration_without_subscribers(self, session, deps, task):
        bot = FakeBot()
        # A guest finishing an empty group is not "everyone finished".
        await record_completion(deps, bot, CHAT, 99, display_name="ضيف")
        assert not any(t in ALL_DONE for t in bot.sent_texts)


class TestGuests:
    async def test_guest_completion_is_recorded_without_stats(self, session, deps, task):
        bot = FakeBot()
        text, newly = await record_completion(deps, bot, CHAT, 99, display_name="ضيف")

        assert newly is True
        assert text in DONE
        assert await TaskRepo(session).has_done(task.id, 99)
        # No streak: a guest is not expected to read daily.
        assert await StatsRepo(session).get(CHAT, 99) is None

    async def test_guest_is_invited_to_subscribe(self, session, deps, task):
        bot = FakeBot()
        await record_completion(deps, bot, CHAT, 99, display_name="ضيف")
        assert any("/join" in t for t in bot.sent_texts)

    async def test_invitation_is_sent_at_most_weekly(self, session, deps, group):
        tasks = TaskRepo(session)
        bot = FakeBot()

        first = await tasks.create(CHAT, task_date=TODAY, page_start=1, page_end=2)
        await record_completion(deps, bot, CHAT, 99, display_name="ضيف", task_id=first.id)
        invites_after_first = sum("/join" in t for t in bot.sent_texts)

        second = await tasks.create(
            CHAT, task_date=TODAY + dt.timedelta(days=1), page_start=3, page_end=4
        )
        await record_completion(deps, bot, CHAT, 99, display_name="ضيف", task_id=second.id)

        assert invites_after_first == 1
        assert sum("/join" in t for t in bot.sent_texts) == 1

    async def test_guest_never_appears_as_pending(self, session, deps, task):
        await subscribe(session, 1)
        await record_completion(deps, FakeBot(), CHAT, 99, display_name="ضيف")
        pending = await TaskRepo(session).pending_subscribers(task.id)
        assert {s.user_id for s in pending} == {1}
