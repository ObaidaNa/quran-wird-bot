"""The catch-up plan in private chat: enrollment, the piece buttons, /catchup.

Enrollment can only begin in the group — a button under the reminders deep-links
here with a `cu_<chat_id>` /start payload — because Telegram lets no bot open a
private chat uninvited. Everything after that happens between the member and
the bot; the group hears about it again only when the debt is gone.

Service-shaped functions take `(deps, bot, ...)` so the tests can drive them
without building Update objects; the handlers at the bottom only unpack.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from ..db.models import CatchupState, utcnow
from ..db.repo import (
    CatchupEventKind,
    CatchupRepo,
    GroupRepo,
    SubscriberRepo,
    TaskRepo,
)
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain import catchup as policy
from ..jobs import catchup as catchup_job
from ..messages import ar
from ..messages import catchup_view as view
from ..messages.phrases import phrases
from ..messages.render import NOUNS, counted

log = logging.getLogger(__name__)

# Deep-link payloads look like `cu_-100123456` — the chat id the plan belongs to.
PAYLOAD_PREFIX = "cu_"

ANSWER_LIMIT = 190


async def _revive(plan, repo: CatchupRepo) -> None:
    """Any sign of life wakes a resting plan."""
    if plan.queue and plan.state != CatchupState.ACTIVE:
        plan.state = CatchupState.ACTIVE
        plan.paused_by_user = False
        plan.probe_count = 0
        plan.ignored_streak = 0
        plan.last_sent_at = utcnow()
        await repo.log(plan.chat_id, plan.user_id, CatchupEventKind.RESUMED)


# --------------------------------------------------------------- enrollment


async def enroll_or_revive(
    deps: Deps, bot: Bot, chat_id: int, user_id: int, *, display_name: str
) -> str | None:
    """Enter the catch-up plan from a deep link, or wake an existing one.

    Returns the text the member should read, or None when the welcome and the
    first piece went straight out.
    """
    send_welcome = False
    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
        subscriber = await SubscriberRepo(session).get(chat_id, user_id)
        if group is None or not group.is_active:
            return ar.CATCHUP_NOT_SUBSCRIBED
        if subscriber is None or not subscriber.is_active:
            return ar.CATCHUP_NOT_SUBSCRIBED

        missed = await TaskRepo(session).missed_closed(
            chat_id,
            user_id,
            since=subscriber.joined_at.replace(tzinfo=dt.UTC)
            .astimezone(ZoneInfo(group.timezone))
            .date(),
        )
        if not missed:
            return ar.CATCHUP_NO_DEBT

        queue = []
        # A later repeat read in the group already covers those pages, even
        # when the member had no private plan at the time.
        for task in missed:
            remaining = [[task.page_start, task.page_end]]
            for read in await TaskRepo(session).completed_since(chat_id, user_id, task.task_date):
                remaining = policy.drop_pages(remaining, read.page_start, read.page_end)
            queue.extend(remaining)
        queue = policy.merge_ranges(queue)

        repo = CatchupRepo(session)
        plan = await repo.get(chat_id, user_id)
        if plan is None:
            plan = await repo.create(
                chat_id,
                user_id,
                queue=queue,
                debt_tasks=[t.id for t in missed],
                cursor=queue[0][0] if queue else 1,
                dose=policy.START_DOSE,
            )
            await repo.log(chat_id, user_id, CatchupEventKind.ENROLLED)
            if not queue:
                await catchup_job.reconcile_plan(session, plan, display_name=display_name)
                return ar.CATCHUP_NO_DEBT
            send_welcome = True
        else:
            await _revive(plan, repo)
        pages_left = policy.count_pages(plan.queue)

    if send_welcome:
        try:
            await bot.send_message(
                user_id,
                ar.CATCHUP_WELCOME.format(pages=counted(pages_left, NOUNS["page"])),
            )
        except TelegramError:
            log.exception("user %s: catch-up welcome failed", user_id)
    await show_piece(deps, bot, chat_id, user_id)
    return None


async def handle_start_payload(
    update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str
) -> bool:
    """Take over a private /start that carries a `cu_` payload."""
    if not payload.startswith(PAYLOAD_PREFIX):
        return False
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or message is None:
        return True
    try:
        chat_id = int(payload[len(PAYLOAD_PREFIX) :])
    except ValueError:
        return True

    text = await enroll_or_revive(
        get_deps(context), context.bot, chat_id, user.id, display_name=user.full_name
    )
    if text is not None:
        await message.reply_text(text)
    return True


# ---------------------------------------------------------------- the buttons


async def complete_pending(
    deps: Deps,
    bot: Bot,
    chat_id: int,
    user_id: int,
    *,
    display_name: str,
    token: int | None = None,
) -> str:
    async with deps.catchup_lock(chat_id, user_id):
        return await _complete_pending(
            deps, bot, chat_id, user_id, display_name=display_name, token=token
        )


async def _complete_pending(
    deps: Deps,
    bot: Bot,
    chat_id: int,
    user_id: int,
    *,
    display_name: str,
    token: int | None = None,
) -> str:
    """The «أتممت المقطع» button. Returns the toast text.

    Pressing it is reading, so it counts in any state — a paused plan revives
    on the way through. Only a second press on the same piece is refused.
    """
    caught_up = False
    async with session_scope(deps.sessions) as session:
        repo = CatchupRepo(session)
        plan = await repo.get(chat_id, user_id)
        if plan is None or plan.pending_start is None or plan.pending_end is None:
            return ar.DONE_ALREADY
        if token is not None and token != plan.pending_token:
            return ar.DONE_ALREADY
        if not await catchup_job.eligible(session, chat_id, user_id):
            return ar.CATCHUP_NOT_SUBSCRIBED
        await _revive(plan, repo)

        group = await GroupRepo(session).get(chat_id)
        local_hour = dt.datetime.now(ZoneInfo(group.timezone)).hour if group is not None else 12

        piece_start, piece_end = plan.pending_start, plan.pending_end
        plan.cursor = piece_end + 1
        plan.pending_start = None
        plan.pending_end = None
        plan.pending_message_id = None
        plan.last_done_at = utcnow()
        plan.done_hours = policy.remember_hour(plan.done_hours or [], local_hour)
        plan.success_streak += 1
        if plan.success_streak >= policy.SUCCESSES_TO_GROW:
            plan.dose = policy.dose_after_success(plan.dose)
            plan.success_streak = 0
        plan.ignored_streak = 0

        plan.queue = policy.drop_pages(plan.queue, piece_start, piece_end)
        plan.cursor = plan.queue[0][0] if plan.queue else piece_end + 1
        await repo.log(
            chat_id,
            user_id,
            CatchupEventKind.PIECE_DONE,
            page_start=piece_start,
            page_end=piece_end,
        )
        caught_up = await catchup_job.reconcile_plan(session, plan, display_name=display_name)

    toast = phrases.done.pick(chat_id)
    if caught_up:
        try:
            await bot.send_message(user_id, ar.CATCHUP_DONE_ALL)
        except TelegramError:
            log.exception("user %s: the caught-up message failed", user_id)
        await catchup_job.announce_caught_up(bot, chat_id, display_name)
        return toast

    # The pull path: the next piece arrives on its own after a gap, but a
    # member on a roll never has to wait for the tick.
    try:
        await bot.send_message(
            user_id, ar.CATCHUP_NEXT_SOON, reply_markup=view.next_piece_keyboard(chat_id)
        )
    except TelegramError:
        log.exception("user %s: the after-done message failed", user_id)
    return toast


async def show_piece(deps: Deps, bot: Bot, chat_id: int, user_id: int) -> str:
    """«أرسل مقطعي»: bring the pending piece back, or push the next one now."""
    async with session_scope(deps.sessions) as session:
        repo = CatchupRepo(session)
        plan = await repo.get(chat_id, user_id)
        if plan is None:
            return ar.CATCHUP_NOT_SUBSCRIBED
        if not await catchup_job.eligible(session, chat_id, user_id):
            return ar.CATCHUP_NOT_SUBSCRIBED
        await _revive(plan, repo)
    sent = await catchup_job.send_piece_now(bot, deps, chat_id, user_id, resend=True)
    return "🌿" if sent else ar.CATCHUP_NO_PENDING


async def set_paused(
    deps: Deps, chat_id: int, user_id: int, *, paused: bool, by_user: bool
) -> str | None:
    """Pause or resume. Returns a text to show, or None when nothing changed."""
    async with session_scope(deps.sessions) as session:
        repo = CatchupRepo(session)
        plan = await repo.get(chat_id, user_id)
        if plan is None:
            return ar.CATCHUP_NOT_SUBSCRIBED
        if not await catchup_job.eligible(session, chat_id, user_id):
            return ar.CATCHUP_NOT_SUBSCRIBED
        if paused:
            if plan.state != CatchupState.ACTIVE:
                return None
            plan.state = CatchupState.PAUSED
            plan.paused_by_user = by_user
            await repo.log(chat_id, user_id, CatchupEventKind.PAUSED)
            return ar.CATCHUP_PAUSED
        if plan.state in (CatchupState.PAUSED, CatchupState.DORMANT, CatchupState.BLOCKED):
            await _revive(plan, repo)
            return ar.CATCHUP_RESUMED
        return None


# ------------------------------------------------------------------ /catchup


async def status_text(deps: Deps, chat_id: int, user_id: int) -> tuple[str, object]:
    """The /catchup reply: text plus whatever keyboard the state calls for."""
    async with session_scope(deps.sessions) as session:
        plan = await CatchupRepo(session).get(chat_id, user_id)
        if plan is None:
            return ar.CATCHUP_NOT_SUBSCRIBED, None
        pages_left = policy.count_pages(plan.queue)
        paused = plan.state in (CatchupState.PAUSED, CatchupState.DORMANT, CatchupState.BLOCKED)
        group = await GroupRepo(session).get(chat_id)
        text = view.status_message(
            pages_left=pages_left,
            dose=plan.dose,
            state_text=view.state_note(plan.state),
            group_title=group.title if group else None,
        )
        return text, view.status_keyboard(chat_id, paused=paused, has_debt=pages_left > 0)


async def catchup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or message is None:
        return
    if chat.type != "private":
        await message.reply_text(ar.CATCHUP_ONLY_PRIVATE)
        return
    deps = get_deps(context)
    async with session_scope(deps.sessions) as session:
        plans = list(await CatchupRepo(session).list_for_user(user.id))
    if not plans:
        await message.reply_text(ar.CATCHUP_NOT_SUBSCRIBED)
        return
    # /catchup is an explicit return, not merely an inspection of an asleep
    # plan. Clear the ignored streak before its next scheduled check.
    for plan in plans:
        await set_paused(deps, plan.chat_id, user.id, paused=False, by_user=False)
        text, keyboard = await status_text(deps, plan.chat_id, user.id)
        await message.reply_text(text, reply_markup=keyboard)


# ------------------------------------------------------------- bare handlers


def _payload(update: Update) -> tuple[int, int] | None:
    """(chat_id, user_id) out of a catch-up callback, or None."""
    query = update.callback_query
    if (
        query is None
        or query.data is None
        or query.from_user is None
        or update.effective_chat is None
        or update.effective_chat.type != "private"
        or update.effective_chat.id != query.from_user.id
    ):
        return None
    _, _, raw = query.data.partition(":")
    try:
        return int(raw.split(":")[0]), query.from_user.id
    except ValueError:
        return None


async def on_done_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ids = _payload(update)
    if query is None or ids is None:
        return
    text = await complete_pending(
        get_deps(context),
        context.bot,
        ids[0],
        ids[1],
        display_name=query.from_user.full_name,
        token=int(query.data.rsplit(":", 1)[1]),
    )
    # The piece's buttons are spent; leave the text as the record of it.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await query.answer(text[:ANSWER_LIMIT])


async def on_show_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ids = _payload(update)
    if query is None or ids is None:
        return
    text = await show_piece(get_deps(context), context.bot, ids[0], ids[1])
    await query.answer(text[:ANSWER_LIMIT])


async def on_pause_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ids = _payload(update)
    if query is None or ids is None:
        return
    text = await set_paused(get_deps(context), ids[0], ids[1], paused=True, by_user=True)
    await query.answer((text or "")[:ANSWER_LIMIT])


async def on_resume_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ids = _payload(update)
    if query is None or ids is None:
        return
    text = await set_paused(get_deps(context), ids[0], ids[1], paused=False, by_user=False)
    if text is not None:
        await show_piece(get_deps(context), context.bot, ids[0], ids[1])
    await query.answer((text or "")[:ANSWER_LIMIT])


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(on_done_button, pattern=rf"^{view.CB_DONE}:-?\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(on_show_button, pattern=rf"^{view.CB_SHOW}:-?\d+$"))
    app.add_handler(CallbackQueryHandler(on_pause_button, pattern=rf"^{view.CB_PAUSE}:-?\d+$"))
    app.add_handler(CallbackQueryHandler(on_resume_button, pattern=rf"^{view.CB_RESUME}:-?\d+$"))
    app.add_handler(CommandHandler("catchup", catchup_command))
