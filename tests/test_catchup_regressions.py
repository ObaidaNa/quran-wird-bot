"""Regression coverage for whole-page catch-up plans and private delivery."""

from __future__ import annotations

import datetime as dt

import pytest
from fakes import FakeBot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram.error import TimedOut
from test_catchup import CHAT, USER, closed_task, enroll, seed_mushaf, subscribe

from quran_wird.db.models import Ayah, TaskMiss, utcnow
from quran_wird.db.repo import CatchupRepo, GroupRepo, StatsRepo, SubscriberRepo, TaskRepo
from quran_wird.domain import catchup as policy
from quran_wird.handlers.catchup import complete_pending, enroll_or_revive, set_paused, show_piece
from quran_wird.handlers.mark_done import record_completion
from quran_wird.jobs.catchup import run_tick
from quran_wird.jobs.close_day import close_day
from quran_wird.messages import ar


@pytest.fixture
async def group(session):
    group, _ = await GroupRepo(session).get_or_create(CHAT)
    group.active_weekdays = list(range(7))
    group.quiet_hours_start = None
    group.quiet_hours_end = None
    await seed_mushaf(session)
    return group


def caught_up_announcements(bot):
    return [
        message
        for message in bot.messages
        if message.chat_id == CHAT and "لحق بالمجموعة" in (message.text or "")
    ]


async def test_old_piece_token_cannot_complete_the_next_piece(session, deps, group):
    await subscribe(session)
    task = await closed_task(session, (3, 4), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    old_token = plan.pending_token

    await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=old_token)
    await show_piece(deps, bot, CHAT, USER)
    assert (plan.pending_start, plan.pending_end) == (4, 4)
    assert plan.pending_token != old_token
    current_token = plan.pending_token
    successes = plan.success_streak

    response = await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=old_token)

    assert response == ar.DONE_ALREADY
    assert (plan.pending_start, plan.pending_end, plan.pending_token) == (4, 4, current_token)
    assert plan.queue == [[4, 4]]
    assert plan.success_streak == successes
    assert not await TaskRepo(session).has_done(task.id, USER)
    assert caught_up_announcements(bot) == []


async def test_remaining_pages_shrink_inside_a_single_debt_range(session, deps, group):
    await subscribe(session)
    task = await closed_task(session, (3, 7), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)

    for page in (3, 4, 5):
        assert (plan.pending_start, plan.pending_end) == (page, page)
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token)
        assert plan.queue == [[page + 1, 7]]
        assert policy.count_pages(plan.queue) == 7 - page
        assert not await TaskRepo(session).has_done(task.id, USER)
        assert (await StatsRepo(session).get(CHAT, USER)).total_missed == 1
        await show_piece(deps, bot, CHAT, USER)

    assert (plan.pending_start, plan.pending_end) == (6, 7)
    assert caught_up_announcements(bot) == []


async def test_dose_grows_only_after_each_three_successes(session, deps, group):
    await subscribe(session)
    await closed_task(session, (1, 11), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    assert plan.dose == 1

    for size, next_dose in zip((1, 1, 1, 2, 2, 2), (1, 1, 2, 2, 2, 3), strict=True):
        assert plan.pending_end - plan.pending_start + 1 == size
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token)
        assert plan.dose == next_dose
        await show_piece(deps, bot, CHAT, USER)

    assert plan.queue == [[10, 11]]
    assert (plan.pending_start, plan.pending_end) == (10, 11)


async def test_group_completion_retires_pending_before_new_debt_arrives(session, deps, group):
    await subscribe(session)
    old_debt = await closed_task(session, (3, 3), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    obsolete_token = plan.pending_token
    today = dt.date.today()
    repeated = await TaskRepo(session).create(CHAT, task_date=today, page_start=3, page_end=3)

    _, newly = await record_completion(
        deps, bot, CHAT, USER, display_name="أحمد", task_id=repeated.id
    )

    assert newly
    assert plan.state == "caught_up"
    assert plan.pending_start is None
    assert plan.pending_end is None
    assert plan.pending_message_id is None
    assert plan.pending_token != obsolete_token
    assert await TaskRepo(session).has_done(old_debt.id, USER)
    assert len(caught_up_announcements(bot)) == 1

    await close_day(bot, deps, CHAT)
    new_debt = await TaskRepo(session).create(
        CHAT, task_date=today + dt.timedelta(days=1), page_start=4, page_end=4
    )
    await close_day(bot, deps, CHAT)
    assert plan.state == "active"
    assert plan.queue == [[4, 4]]
    now = utcnow()
    plan.last_sent_at = now - policy.NEXT_PIECE_GAP - dt.timedelta(minutes=1)
    await session.flush()

    assert await run_tick(bot, deps, now=now) == 1
    assert (plan.pending_start, plan.pending_end) == (4, 4)
    assert (
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=obsolete_token)
        == ar.DONE_ALREADY
    )
    assert not await TaskRepo(session).has_done(new_debt.id, USER)
    assert len(caught_up_announcements(bot)) == 1


@pytest.mark.parametrize("action", ["show", "resume", "complete", "enroll"])
async def test_unsubscribed_member_cannot_use_private_actions(session, deps, group, action):
    await subscribe(session)
    task = await closed_task(session, (3, 4), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    await set_paused(deps, CHAT, USER, paused=True, by_user=True)
    await SubscriberRepo(session).unsubscribe(CHAT, USER)
    before = (plan.pending_start, plan.pending_end, plan.pending_token, plan.success_streak)
    messages_before = len(bot.messages)
    photos_before = len(bot.photos)

    if action == "show":
        response = await show_piece(deps, bot, CHAT, USER)
    elif action == "resume":
        response = await set_paused(deps, CHAT, USER, paused=False, by_user=True)
    elif action == "complete":
        response = await complete_pending(
            deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token
        )
    else:
        response = await enroll_or_revive(deps, bot, CHAT, USER, display_name="أحمد")

    assert response == ar.CATCHUP_NOT_SUBSCRIBED
    assert plan.state == "paused"
    assert plan.queue == [[3, 4]]
    assert (plan.pending_start, plan.pending_end, plan.pending_token, plan.success_streak) == before
    assert not await TaskRepo(session).has_done(task.id, USER)
    assert await run_tick(bot, deps, now=utcnow() + dt.timedelta(days=30)) == 0
    assert len(bot.messages) == messages_before
    assert len(bot.photos) == photos_before
    assert bot.albums == []


async def test_read_prefix_is_not_caught_up_and_final_announcement_is_not_duplicated(
    session, deps, group
):
    await subscribe(session)
    first = await closed_task(session, (3, 4), days_ago=2)
    last = await closed_task(session, (7, 7), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)

    for page in (3, 4):
        assert plan.pending_start == page
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token)
        assert plan.state == "active"
        assert caught_up_announcements(bot) == []
        await show_piece(deps, bot, CHAT, USER)

    assert await TaskRepo(session).has_done(first.id, USER)
    assert not await TaskRepo(session).has_done(last.id, USER)
    assert plan.queue == [[7, 7]]
    final_token = plan.pending_token
    await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=final_token)
    assert plan.state == "caught_up"
    assert len(caught_up_announcements(bot)) == 1

    assert (
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=final_token)
        == ar.DONE_ALREADY
    )
    assert await enroll_or_revive(deps, bot, CHAT, USER, display_name="أحمد") == ar.CATCHUP_NO_DEBT
    assert await run_tick(bot, deps, now=utcnow() + dt.timedelta(days=30)) == 0
    assert len(caught_up_announcements(bot)) == 1


async def test_lower_new_debt_is_not_completed_by_finishing_higher_pending_pages(
    session, deps, group
):
    await subscribe(session)
    high = await closed_task(session, (8, 9), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    assert (plan.pending_start, plan.pending_end) == (8, 8)
    lower = await TaskRepo(session).create(
        CHAT, task_date=dt.date.today(), page_start=1, page_end=2
    )
    await close_day(bot, deps, CHAT)
    assert plan.queue == [[1, 2], [8, 9]]

    await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token)

    assert plan.queue == [[1, 2], [9, 9]]
    assert not await TaskRepo(session).has_done(lower.id, USER)
    assert not await TaskRepo(session).has_done(high.id, USER)
    assert (await StatsRepo(session).get(CHAT, USER)).total_missed == 2
    for page in (1, 2):
        await show_piece(deps, bot, CHAT, USER)
        assert (plan.pending_start, plan.pending_end) == (page, page)
        assert not await TaskRepo(session).has_done(lower.id, USER)
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token)

    assert await TaskRepo(session).has_done(lower.id, USER)
    assert not await TaskRepo(session).has_done(high.id, USER)
    assert plan.queue == [[9, 9]]
    assert plan.state == "active"
    assert caught_up_announcements(bot) == []


@pytest.mark.parametrize("action", ["resume", "show", "enroll"])
async def test_returning_to_auto_paused_plan_resets_pause_threshold(session, deps, group, action):
    await subscribe(session)
    await closed_task(session, (3, 4), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    plan.ignored_streak = policy.AUTO_PAUSE_AFTER
    await session.flush()
    await run_tick(bot, deps, now=utcnow())
    assert plan.state == "paused"
    assert not plan.paused_by_user

    if action == "resume":
        assert await set_paused(deps, CHAT, USER, paused=False, by_user=True) == ar.CATCHUP_RESUMED
    elif action == "show":
        await show_piece(deps, bot, CHAT, USER)
    else:
        await enroll_or_revive(deps, bot, CHAT, USER, display_name="أحمد")

    assert plan.state == "active"
    assert plan.ignored_streak == 0
    assert plan.probe_count == 0
    messages_before = len(bot.messages)
    assert await run_tick(bot, deps, now=utcnow()) == 0
    assert plan.state == "active"
    assert len(bot.messages) == messages_before


@pytest.mark.parametrize("dose", [1, 3])
async def test_original_photos_and_explicit_resends_never_include_ayah_text(
    session, deps, group, dose
):
    await subscribe(session)
    await closed_task(session, (3, 7), days_ago=1)
    bot = FakeBot()
    plan = await enroll(deps, bot)
    if dose > 1:
        await complete_pending(deps, bot, CHAT, USER, display_name="أحمد", token=plan.pending_token)
        plan.dose = dose
        await show_piece(deps, bot, CHAT, USER)
    pending = (plan.pending_start, plan.pending_end, plan.pending_token)
    assert plan.pending_end - plan.pending_start + 1 == dose
    photos_before = len(bot.photos)
    albums_before = len(bot.albums)

    await show_piece(deps, bot, CHAT, USER)

    assert (plan.pending_start, plan.pending_end, plan.pending_token) == pending
    if dose == 1:
        assert len(bot.photos) == photos_before + 1
        assert all(photo["chat_id"] == USER for photo in bot.photos)
    else:
        assert len(bot.albums) == albums_before + 1
        assert len(bot.albums[-1]) == dose
    texts = [message.text or "" for message in bot.messages]
    texts.extend(photo["caption"] or "" for photo in bot.photos)
    texts.extend(media.caption or "" for album in bot.albums for media in album)
    assert texts
    assert all(message.chat_id == USER for message in bot.messages)
    for ayah_text in await session.scalars(select(Ayah.text)):
        assert not any(ayah_text in text for text in texts)


async def test_failure_after_photos_keeps_committed_claim_without_automatic_duplicate(
    session, deps, group
):
    await subscribe(session)
    await closed_task(session, (3, 4), days_ago=1)
    plan = await enroll(deps, FakeBot())
    await complete_pending(
        deps, FakeBot(), CHAT, USER, display_name="أحمد", token=plan.pending_token
    )
    now = utcnow()
    plan.last_done_at = now - policy.NEXT_PIECE_GAP - dt.timedelta(minutes=1)
    await session.commit()

    class FailAfterPhotos(FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            assert chat_id == USER
            assert len(self.photos) == 1
            async with AsyncSession(session.bind, expire_on_commit=False) as observer:
                durable = await CatchupRepo(observer).get(CHAT, USER)
                assert (durable.pending_start, durable.pending_end) == (4, 4)
                assert durable.last_sent_at is not None
                assert durable.pending_token > 0
            raise TimedOut("message delivery ambiguous after the photo")

    bot = FailAfterPhotos()
    assert await run_tick(bot, deps, now=now) == 0
    await session.rollback()
    plan = await CatchupRepo(session).get(CHAT, USER)
    token = plan.pending_token
    assert (plan.pending_start, plan.pending_end) == (4, 4)
    assert plan.pending_message_id is None
    assert plan.queue == [[4, 4]]

    assert await run_tick(bot, deps, now=now) == 0
    assert len(bot.photos) == 1
    assert bot.albums == []
    assert bot.messages == []
    assert plan.pending_token == token

    resend_bot = FakeBot()
    await show_piece(deps, resend_bot, CHAT, USER)
    assert len(resend_bot.photos) == 1
    assert (plan.pending_start, plan.pending_end, plan.pending_token) == (4, 4, token)


@pytest.mark.parametrize("existing_miss", [False, True])
async def test_closed_days_without_miss_while_unsubscribed_never_become_debt(
    session, deps, group, existing_miss
):
    await subscribe(session)
    if existing_miss:
        missed = await closed_task(session, (3, 3), days_ago=1)
    await SubscriberRepo(session).unsubscribe(CHAT, USER)
    away = await TaskRepo(session).create(CHAT, task_date=dt.date.today(), page_start=5, page_end=6)
    await close_day(FakeBot(), deps, CHAT)
    assert await session.get(TaskMiss, (away.id, USER)) is None
    await SubscriberRepo(session).subscribe(CHAT, USER, display_name="أحمد")
    bot = FakeBot()

    response = await enroll_or_revive(deps, bot, CHAT, USER, display_name="أحمد")

    plan = await CatchupRepo(session).get(CHAT, USER)
    if existing_miss:
        assert response is None
        assert plan.queue == [[3, 3]]
        assert plan.debt_tasks == [missed.id]
        assert (plan.pending_start, plan.pending_end) == (3, 3)
    else:
        assert response == ar.CATCHUP_NO_DEBT
        assert plan is None
        assert bot.messages == []
        assert bot.photos == []
