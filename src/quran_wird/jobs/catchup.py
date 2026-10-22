"""The private catch-up plan: pieces, nudges, and the tick that drives them.

One member's plan is a queue of missed whole pages. A single repeating job walks every
active plan every half hour and asks `domain.catchup.decide` what to do; all
timing policy lives there, this file only executes it.

Automatic attempts are committed before contacting Telegram. A crash or an
ambiguous timeout can lose a delivery, but does not replay it automatically.
The member can explicitly resend the same pending piece without advancing it.

`Forbidden` in a private chat means the member blocked the bot. That is not a
group problem — the group stays active — so the plan itself goes to BLOCKED
and waits for the member's own /start to revive it.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes, JobQueue

from ..db.models import CatchupPlan, CatchupState, CompletionSource, utcnow
from ..db.repo import (
    CatchupEventKind,
    CatchupRepo,
    GroupRepo,
    MediaRepo,
    StatsRepo,
    SubscriberRepo,
    TaskRepo,
)
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain import catchup as policy
from ..domain.catchup import TickAction
from ..messages import ar
from ..messages import catchup_view as view
from ..messages.phrases import phrases
from ..tg.media import PageImageMissing, send_pages
from ..tg.mentions import safe_name
from .remind import in_quiet_hours

log = logging.getLogger(__name__)

TICK_JOB = "catchup_tick"
TICK_INTERVAL = dt.timedelta(minutes=30)


async def eligible(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    """Every private action remains linked to an active group subscription."""
    group = await GroupRepo(session).get(chat_id)
    return bool(
        group is not None
        and group.is_active
        and await SubscriberRepo(session).is_active(chat_id, user_id)
    )


# --------------------------------------------------------------- sending


async def _deliver_piece(
    bot: Bot, deps: Deps, session: AsyncSession, plan: CatchupPlan, *, resend: bool = False
) -> bool:
    """Claim and send the plan's next piece, inside the caller's transaction.

    Commit the pending range and attempt timestamp before contacting Telegram.
    Network failure cannot roll back a claim and resend photos on every tick.
    """
    existing = resend and plan.pending_start is not None and plan.pending_end is not None
    if existing:
        found = (plan.pending_start, plan.pending_end)
    else:
        found = policy.next_piece(plan.queue, plan.queue[0][0], plan.dose) if plan.queue else None
    if found is None:
        return False
    start, end = found
    pages = list(range(start, end + 1))
    if not existing:
        plan.pending_token += 1
        plan.pending_start, plan.pending_end = start, end
        await CatchupRepo(session).log(
            plan.chat_id,
            plan.user_id,
            CatchupEventKind.PIECE_SENT,
            page_start=start,
            page_end=end,
        )
    plan.last_sent_at = utcnow()
    group = await GroupRepo(session).get(plan.chat_id)
    await session.commit()
    await send_pages(
        bot,
        plan.user_id,
        pages,
        pages_dir=deps.settings.pages_dir,
        media=MediaRepo(session),
        caption=view.piece_caption(pages, group.title if group else None),
    )
    message = await bot.send_message(
        plan.user_id,
        view.piece_progress_note(policy.count_pages(plan.queue)),
        reply_markup=view.piece_keyboard(plan.chat_id, plan.pending_token),
    )
    plan.pending_message_id = message.message_id
    return True


async def send_piece_now(
    bot: Bot, deps: Deps, chat_id: int, user_id: int, *, resend: bool = False
) -> bool:
    async with deps.catchup_lock(chat_id, user_id):
        return await _send_piece_now(bot, deps, chat_id, user_id, resend=resend)


async def _send_piece_now(
    bot: Bot, deps: Deps, chat_id: int, user_id: int, *, resend: bool = False
) -> bool:
    """Push (or pull) the next piece, if the plan is active and none is out."""
    async with session_scope(deps.sessions) as session:
        plan = await CatchupRepo(session).get(chat_id, user_id)
        if (
            plan is None
            or plan.state != CatchupState.ACTIVE
            or not await eligible(session, chat_id, user_id)
            or (plan.pending_start is not None and not resend)
        ):
            return False
        return await _deliver_piece(bot, deps, session, plan, resend=resend)


# ---------------------------------------------------- repair and caught-up


async def reconcile_plan(
    session: AsyncSession,
    plan: CatchupPlan,
    *,
    display_name: str,
) -> bool:
    """Repair whatever the member has read, and notice when the debt is gone.

    Call after anything that moves the cursor or shrinks the queue: a finished
    private piece, or a group wird the member completed whose pages were still
    owed. A missed day is repaired — a late completion on the old task, and the
    miss moved to the done column — once none of its pages remain in the queue.

    Returns True when the debt hit zero here, so the caller can celebrate.
    """
    tasks = TaskRepo(session)
    stats = StatsRepo(session)
    repo = CatchupRepo(session)

    still_owed: list[int] = []
    for task_id in plan.debt_tasks:
        task = await tasks.get(task_id)
        if task is None:
            # The wird it belonged to was replaced; its pages left the queue
            # with it, so there is nothing to repair.
            continue
        owed = any(
            task.page_start <= q_end and q_start <= task.page_end for q_start, q_end in plan.queue
        )
        if owed:
            still_owed.append(task_id)
            continue
        repaired = await tasks.mark_done(
            task_id, plan.user_id, was_subscriber=True, source=CompletionSource.CATCHUP
        )
        if repaired:
            await stats.repair_missed(plan.chat_id, plan.user_id, task_id=task_id)
            await repo.log(plan.chat_id, plan.user_id, CatchupEventKind.REPAIRED_DAY)
    plan.debt_tasks = still_owed

    if plan.queue:
        return False

    if plan.state == CatchupState.CAUGHT_UP:
        return False
    plan.pending_start = None
    plan.pending_end = None
    plan.pending_message_id = None
    plan.pending_token += 1
    plan.state = CatchupState.CAUGHT_UP
    plan.caught_up_at = utcnow()
    member_stats = await stats.get(plan.chat_id, plan.user_id)
    if member_stats is not None:
        member_stats.consecutive_missed = 0
    await repo.log(plan.chat_id, plan.user_id, CatchupEventKind.CAUGHT_UP)
    return True


async def announce_caught_up(bot: Bot, chat_id: int, display_name: str) -> None:
    """Tell the group one of its own made it back. Best-effort: the celebration
    must never fail the completion that earned it."""
    try:
        await bot.send_message(
            chat_id, ar.CATCHUP_CAUGHT_UP_GROUP.format(name=safe_name(display_name))
        )
    except TelegramError:
        log.exception("chat %s: could not announce a caught-up member", chat_id)


# ------------------------------------------------------------------ the tick


def schedule_tick(job_queue: JobQueue) -> None:
    """The one job that drives every plan; global, not per group."""
    for job in job_queue.jobs(pattern=f"^{TICK_JOB}$"):
        job.schedule_removal()
    job_queue.run_repeating(
        job_catchup_tick,
        interval=TICK_INTERVAL,
        first=TICK_INTERVAL,
        name=TICK_JOB,
    )


async def run_tick(bot: Bot, deps: Deps, *, now: dt.datetime | None = None) -> int:
    """One pass over every plan that might need a message. Returns sends made."""
    now = now or utcnow()
    async with session_scope(deps.sessions) as session:
        plan_keys = [(p.chat_id, p.user_id) for p in await CatchupRepo(session).list_active()]

    sent = 0
    for chat_id, user_id in plan_keys:
        try:
            sent += await _process_plan(bot, deps, chat_id, user_id, now)
        except Forbidden:
            # The member blocked the bot in private; the plan waits for them.
            async with session_scope(deps.sessions) as session:
                plan = await CatchupRepo(session).get(chat_id, user_id)
                if plan is not None:
                    plan.state = CatchupState.BLOCKED
            log.info("chat %s user %s: blocked in private; plan paused", chat_id, user_id)
        except (TelegramError, PageImageMissing):
            log.exception("chat %s user %s: catch-up tick failed", chat_id, user_id)
    return sent


async def _process_plan(bot: Bot, deps: Deps, chat_id: int, user_id: int, now: dt.datetime) -> int:
    async with deps.catchup_lock(chat_id, user_id):
        return await _process_locked_plan(bot, deps, chat_id, user_id, now)


async def _process_locked_plan(
    bot: Bot, deps: Deps, chat_id: int, user_id: int, now: dt.datetime
) -> int:
    async with session_scope(deps.sessions) as session:
        plan = await CatchupRepo(session).get(chat_id, user_id)
        if plan is None:
            return 0
        group = await GroupRepo(session).get(chat_id)
        if group is None or not group.is_active:
            return 0
        # A member who left the group owes it nothing while away.
        if not await SubscriberRepo(session).is_active(chat_id, user_id):
            return 0

        tz = ZoneInfo(group.timezone)
        local_now = now.replace(tzinfo=dt.UTC).astimezone(tz)
        last_utc = plan.last_done_at or plan.last_sent_at or plan.enrolled_at
        last_local = last_utc.replace(tzinfo=dt.UTC).astimezone(tz)

        action = policy.decide(
            plan,
            now=now,
            local_now=local_now,
            last_local=last_local,
            in_quiet=in_quiet_hours(
                local_now.time(), group.quiet_hours_start, group.quiet_hours_end
            ),
        )
        repo = CatchupRepo(session)

        if action == TickAction.SKIP:
            return 0

        if action == TickAction.DORMANT:
            plan.state = CatchupState.DORMANT
            await repo.log(chat_id, user_id, CatchupEventKind.DORMANT)
            log.info(
                "chat %s user %s: plan dormant after %s probes",
                chat_id,
                user_id,
                plan.probe_count,
            )
            return 0

        if action == TickAction.PAUSE_NOTICE:
            plan.state = CatchupState.PAUSED
            plan.paused_by_user = False
            plan.last_probe_at = now
            plan.probe_count = 0
            await repo.log(chat_id, user_id, CatchupEventKind.PAUSED)
            await session.commit()
            await bot.send_message(user_id, ar.CATCHUP_AUTO_PAUSED)
            return 1

        if action == TickAction.PROBE:
            plan.probe_count += 1
            plan.last_probe_at = now
            await repo.log(chat_id, user_id, CatchupEventKind.NUDGE)
            await session.commit()
            text, keyboard = view.nudge_message(
                phrases.catchup_probe.pick((chat_id, user_id)), chat_id
            )
            await bot.send_message(user_id, text, reply_markup=keyboard)
            return 1

        if action == TickAction.NUDGE:
            plan.ignored_streak += 1
            plan.tone_index += 1
            plan.dose = policy.dose_after_lapse(plan.dose)
            plan.success_streak = 0
            if (
                plan.pending_start is not None
                and plan.pending_end is not None
                and plan.pending_end - plan.pending_start + 1 > plan.dose
            ):
                # Offer a genuinely smaller current piece, not only a smaller
                # hypothetical next one. Retire the larger piece's old button.
                plan.pending_end = plan.pending_start + plan.dose - 1
                plan.pending_token += 1
                plan.pending_message_id = None
            plan.last_sent_at = now
            await repo.log(chat_id, user_id, CatchupEventKind.NUDGE)
            await session.commit()
            text, keyboard = view.nudge_message(
                phrases.catchup_tone(plan.tone_index).pick((chat_id, user_id)), chat_id
            )
            await bot.send_message(user_id, text, reply_markup=keyboard)
            return 1

        # TickAction.PIECE
        return int(await _deliver_piece(bot, deps, session, plan))


async def job_catchup_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_tick(context.bot, get_deps(context))
