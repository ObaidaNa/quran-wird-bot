"""Correcting the wird that went out before the group had configured the bot.

The bot posts a wird the moment it joins, and it can only guess: page one. A
group resuming its own khatmah answers with `/setpage`, and before this existed
that correction reached tomorrow only — the group spent its whole first day on
pages it had already read, with `/sendnow` answering "already sent".
"""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot
from telegram import Update
from test_e2e import CHAT, StubContext, StubQueue, group_chat, index, membership_update

from quran_wird.db.models import CompletionSource
from quran_wird.db.repo import GroupRepo, SubscriberRepo, TaskRepo
from quran_wird.handlers.lifecycle import on_my_chat_member
from quran_wird.handlers.wird import on_replace_button
from quran_wird.jobs.close_day import close_day
from quran_wird.jobs.send_daily import replace_wird, replaceable_today, send_wird
from quran_wird.messages import ar, render

__all__ = ["index"]  # re-exported fixture

TODAY = dt.date.today()


async def join(deps, bot=None, queue=None) -> tuple[FakeBot, StubQueue]:
    """Put the group through the real path: the bot is added and posts page 1-2."""
    bot = bot or FakeBot()
    queue = queue or StubQueue()
    await on_my_chat_member(
        Update(update_id=1, my_chat_member=membership_update(group_chat(), joining=True)),
        StubContext(bot, deps, queue),
    )
    return bot, queue


class FakeQuery:
    """A callback query the handler can drive without a live Bot.

    `CallbackQuery.answer()` and `.edit_message_text()` are shortcuts that reach
    for the Bot the object was built with, which a fake cannot supply.
    """

    def __init__(self, data: str) -> None:
        self.data = data
        self.answers: list[str | None] = []
        self.edits: list[str] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self, query: FakeQuery) -> None:
        self.callback_query = query
        self.effective_chat = group_chat()


def press(bot, deps, queue, task_id: int) -> tuple[FakeUpdate, StubContext]:
    """The admin pressing the confirm button under the offer."""
    query = FakeQuery(f"{render.CB_REPLACE}:{task_id}")
    return FakeUpdate(query), StubContext(bot, deps, queue)


# ------------------------------------------------------------------- the offer


class TestOffer:
    async def test_a_corrected_page_makes_todays_wird_replaceable(self, session, deps, index):
        await join(deps)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        offer = await replaceable_today(deps, CHAT)
        assert offer is not None and offer.differs
        assert (offer.current.start, offer.current.end) == (1, 2)
        assert (offer.proposed.start, offer.proposed.end) == (10, 11)

    async def test_a_new_pace_makes_todays_wird_replaceable(self, session, deps, index):
        # The same trap by another route: the group keeps page 1 but wants five
        # pages a day, and the two-page wird is already above them.
        await join(deps)
        group = await GroupRepo(session).get(CHAT)
        group.pages_per_day = 5
        await session.flush()

        offer = await replaceable_today(deps, CHAT)
        assert offer is not None and offer.differs
        assert (offer.proposed.start, offer.proposed.end) == (1, 5)

    async def test_an_unchanged_group_is_offered_nothing(self, session, deps, index):
        # Nothing was configured, so the wird above them is already the right one
        # and /sendnow should still say plainly that it went out.
        await join(deps)
        offer = await replaceable_today(deps, CHAT)
        assert offer is not None and not offer.differs

    async def test_a_closed_day_is_not_replaceable(self, session, deps, index):
        """Once the day is counted its wird is history, not a setting.

        Replacing it would mean re-counting completions and moving the page
        pointer a second time.
        """
        bot, _ = await join(deps)
        await close_day(bot, deps, CHAT)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        assert await replaceable_today(deps, CHAT) is None

    async def test_a_day_with_no_wird_is_not_replaceable(self, session, deps, index):
        await GroupRepo(session).get_or_create(CHAT)
        await session.flush()
        assert await replaceable_today(deps, CHAT) is None

    async def test_the_offer_counts_who_already_pressed(self, session, deps, index):
        await join(deps)
        task = await TaskRepo(session).get_open(CHAT)
        await TaskRepo(session).mark_done(task.id, 77, was_subscriber=True)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        offer = await replaceable_today(deps, CHAT)
        # So the admin is warned before dropping someone's tick, rather than after.
        assert offer.done_count == 1
        assert "١" in render.replace_offer(offer)


# ------------------------------------------------------------- the replacement


class TestReplaceWird:
    async def test_the_reported_flow_ends_on_the_right_pages(self, session, deps, index):
        """Added at page 1-2, `/setpage 10`, confirm — today reads 10-11.

        This is the bug as a group met it: before, the only way out was to wait
        for the next morning.
        """
        bot, _ = await join(deps)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        offer = await replaceable_today(deps, CHAT)
        new_id = await replace_wird(bot, deps, CHAT, offer.task_id)

        task = await TaskRepo(session).get_by_date(CHAT, TODAY)
        assert task is not None and task.id == new_id
        assert (task.page_start, task.page_end) == (10, 11)
        assert not task.is_closed

    async def test_the_group_is_left_with_one_wird_for_the_day(self, session, deps, index):
        bot, _ = await join(deps)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()
        offer = await replaceable_today(deps, CHAT)

        await replace_wird(bot, deps, CHAT, offer.task_id)

        assert len(await TaskRepo(session).list_between(CHAT, TODAY, TODAY)) == 1

    async def test_the_withdrawn_wird_is_taken_out_of_the_chat(self, session, deps, index):
        """Both album photos and the wird message go.

        Left in place they would be a second wird whose buttons answer for a
        task that no longer exists.
        """
        bot, _ = await join(deps)
        old = await TaskRepo(session).get_open(CHAT)
        old_ids = [*old.album_message_ids, old.message_id]
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        await replace_wird(bot, deps, CHAT, old.id)

        assert sorted(bot.deleted) == sorted(old_ids)

    async def test_the_replacement_is_sent_before_the_old_one_is_deleted(
        self, session, deps, index, pages_dir
    ):
        """A send that fails must leave the group with the wird it had.

        Deleting first would take the wird away and put nothing in its place.
        """
        bot, _ = await join(deps)
        old = await TaskRepo(session).get_open(CHAT)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()
        for image in pages_dir.glob("*.png"):
            image.unlink()

        from quran_wird.tg.media import PageImageMissing

        with pytest.raises(PageImageMissing):
            await replace_wird(bot, deps, CHAT, old.id)

        assert bot.deleted == []

    async def test_a_stale_button_replaces_nothing(self, session, deps, index):
        """Two admins pressing, or one pressing twice: the second does nothing.

        The task id alone cannot carry this. SQLite hands the replacement the
        rowid the withdrawn wird just gave up, so a stale button often names the
        *new* task. What stops it is that the new task already matches the
        settings, leaving nothing to correct.
        """
        bot, _ = await join(deps)
        old = await TaskRepo(session).get_open(CHAT)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        assert await replace_wird(bot, deps, CHAT, old.id) is not None
        assert await replace_wird(bot, deps, CHAT, old.id) is None

    async def test_a_button_from_another_chat_is_refused(self, session, deps, index):
        bot, _ = await join(deps)
        task = await TaskRepo(session).get_open(CHAT)
        await GroupRepo(session).get_or_create(-999)
        await session.flush()

        assert await replace_wird(bot, deps, -999, task.id) is None

    async def test_the_new_wird_starts_with_an_empty_finisher_list(self, session, deps, index):
        """Nobody has read the corrected pages yet, whoever ticked the old ones."""
        bot, _ = await join(deps)
        old = await TaskRepo(session).get_open(CHAT)
        await SubscriberRepo(session).subscribe(CHAT, 77, display_name="أحمد")
        await TaskRepo(session).mark_done(
            old.id, 77, was_subscriber=True, source=CompletionSource.BUTTON
        )
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        new_id = await replace_wird(bot, deps, CHAT, old.id)

        assert await TaskRepo(session).done_count(new_id) == 0

    async def test_discarding_takes_the_ticks_and_the_reminder_log_with_it(
        self, session, deps, index
    ):
        """Nothing may outlive the wird it was recorded against.

        A completion left behind would count towards a wird whose pages are no
        longer posted, and a reminder log row would silence a reminder for the
        replacement.
        """
        await join(deps)
        tasks = TaskRepo(session)
        old = await tasks.get_open(CHAT)
        await tasks.mark_done(old.id, 77, was_subscriber=False)
        await tasks.log_reminder(old.id, 1, message_ids=[5])
        await session.flush()

        assert await tasks.discard(old.id) is True

        assert await tasks.get(old.id) is None
        assert await tasks.done_count(old.id) == 0
        assert not await tasks.reminder_sent(old.id, 1)
        assert await tasks.discard(old.id) is False


# ---------------------------------------------------------------- the handler


class TestReplaceButton:
    async def test_pressing_it_swaps_the_wird_and_requeues_the_reminders(
        self, session, deps, index, monkeypatch
    ):
        monkeypatch.setattr("quran_wird.handlers.wird.is_group_admin", _is_admin)
        bot, queue = await join(deps)
        old = await TaskRepo(session).get_open(CHAT)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        update, context = press(bot, deps, queue, old.id)
        query = update.callback_query
        await on_replace_button(update, context)

        task = await TaskRepo(session).get_by_date(CHAT, TODAY)
        assert (task.page_start, task.page_end) == (10, 11)
        assert query.edits == [ar.REPLACE_DONE_NOTICE]

        # Every queued reminder must name the wird that is actually posted, or
        # members are chased about pages the group is no longer reading.
        queued = queue.names(f"remind:{CHAT}:")
        assert queued
        assert all(queue.scheduled[n]["data"]["task_id"] == task.id for n in queued)

    async def test_a_member_cannot_press_it(self, session, deps, index, monkeypatch):
        bot, queue = await join(deps)
        old = await TaskRepo(session).get_open(CHAT)
        await GroupRepo(session).set_current_page(CHAT, 10)
        await session.flush()

        monkeypatch.setattr("quran_wird.handlers.wird.is_group_admin", _not_admin)
        update, context = press(bot, deps, queue, old.id)
        await on_replace_button(update, context)

        task = await TaskRepo(session).get_by_date(CHAT, TODAY)
        assert (task.page_start, task.page_end) == (1, 2)


async def _is_admin(update) -> bool:
    return True


async def _not_admin(update) -> bool:
    return False


# --------------------------------------------------------- untouched paths


class TestOrdinaryDaysAreUnaffected:
    async def test_a_group_reading_on_schedule_is_offered_nothing(self, session, deps, index):
        """Day two, sent by the daily job: the wird matches the settings.

        The offer must not appear on an ordinary morning, or every `/sendnow`
        would invite an admin to re-post the wird the group is reading.
        """
        bot, _ = await join(deps)
        await SubscriberRepo(session).subscribe(CHAT, 77, display_name="أحمد")
        first = await TaskRepo(session).get_open(CHAT)
        await TaskRepo(session).mark_done(first.id, 77, was_subscriber=True)
        await session.flush()
        await close_day(bot, deps, CHAT)

        group = await GroupRepo(session).get(CHAT)
        assert group.current_page == 3
        # Yesterday, so the daily job has today to itself.
        first.task_date = TODAY - dt.timedelta(days=1)
        await session.flush()

        await send_wird(bot, deps, CHAT, force=True)
        offer = await replaceable_today(deps, CHAT)
        assert offer is not None and not offer.differs
        assert (offer.current.start, offer.current.end) == (3, 4)
