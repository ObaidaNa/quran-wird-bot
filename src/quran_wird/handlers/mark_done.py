"""Marking the wird as read: the ✅ button, /done, and the "who finished" button."""

from __future__ import annotations

import logging

from telegram import Bot, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

from ..db.models import CompletionSource, DailyTask
from ..db.repo import NudgeRepo, StatsRepo, SubscriberRepo, TaskRepo
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..jobs.send_daily import build_view
from ..messages import ar, render
from ..messages.phrases import NUDGE, phrases
from ..tg.mentions import safe_name

log = logging.getLogger(__name__)

# Telegram truncates callback answers; keep well under the 200-character limit.
ANSWER_LIMIT = 190


async def _refresh_message(bot: Bot, task: DailyTask, view) -> None:
    """Rewrite the wird message so the finisher list is current.

    Several people can press within the same second; each edit rebuilds from the
    database, so the last one still writes the complete list. "Message is not
    modified" means another edit already wrote identical text, which is fine.
    """
    if task.message_id is None:
        return
    try:
        await bot.edit_message_text(
            chat_id=task.chat_id,
            message_id=task.message_id,
            text=render.wird_message(view, phrases.send.pick(task.chat_id)),
            reply_markup=render.wird_keyboard(task.id),
        )
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.warning("chat %s: could not refresh the wird message (%s)", task.chat_id, exc)
    except TelegramError:
        log.exception("chat %s: refreshing the wird message failed", task.chat_id)


async def record_completion(
    deps: Deps,
    bot: Bot,
    chat_id: int,
    user_id: int,
    *,
    display_name: str,
    task_id: int | None = None,
    source: CompletionSource = CompletionSource.BUTTON,
) -> tuple[str, bool]:
    """Record one member finishing the wird.

    Returns (reply text, whether it was newly recorded). The caller decides how
    to deliver the text — a toast for a button press, a reply for /done.
    """
    everyone_done = False
    nudge_name: str | None = None
    caught_up = False

    async with session_scope(deps.sessions) as session:
        tasks = TaskRepo(session)
        task = await tasks.get(task_id) if task_id else await tasks.get_open(chat_id)

        if task is None or task.chat_id != chat_id:
            return ar.NO_OPEN_WIRD, False
        if task.is_closed:
            return ar.WIRD_CLOSED, False

        subs = SubscriberRepo(session)
        subscriber = await subs.get(chat_id, user_id)
        is_subscriber = subscriber is not None and subscriber.is_active
        if is_subscriber:
            # Names drift as people rename themselves on Telegram.
            await subs.touch_name(chat_id, user_id, display_name=display_name)

        newly = await tasks.mark_done(task.id, user_id, was_subscriber=is_subscriber, source=source)
        if not newly:
            return ar.DONE_ALREADY, False

        # Streaks are a subscriber concept: a guest who reads once is not
        # expected daily, so counting them would make "missed a day" meaningless.
        if is_subscriber:
            await StatsRepo(session).record_done(chat_id, user_id, task.task_date)
            # Pages read with the group drop out of the member's catch-up debt
            # — a repeated wird, or the group lapping them in a new khatmah.
            caught_up = await _reconcile_catchup(
                session, chat_id, user_id, task, display_name=display_name
            )
        else:
            nudges = NudgeRepo(session)
            if await nudges.due(chat_id, user_id):
                nudge_name = display_name
                await nudges.record(chat_id, user_id)

        view = await build_view(session, task)
        everyone_done = view.subscriber_count > 0 and not await tasks.pending_subscribers(task.id)
        await _refresh_message(bot, task, view)

    if everyone_done:
        try:
            await bot.send_message(chat_id, phrases.all_done.pick(chat_id))
        except TelegramError:
            log.exception("chat %s: could not send the all-done message", chat_id)

    if nudge_name is not None:
        try:
            await bot.send_message(chat_id, NUDGE.format(name=safe_name(nudge_name)))
        except TelegramError:
            log.exception("chat %s: could not send the subscribe invitation", chat_id)

    if caught_up:
        from ..jobs.catchup import announce_caught_up

        await announce_caught_up(bot, chat_id, display_name)

    return phrases.done.pick(chat_id), True


async def _reconcile_catchup(
    session, chat_id: int, user_id: int, task: DailyTask, *, display_name: str
) -> bool:
    """Fold a group completion into the member's catch-up plan, if any.

    Pages read with the group are no longer owed: they drop out of the queue,
    and any old missed day they complete is repaired. Returns True when the
    debt hit zero here, so the caller can announce it.
    """
    from ..db.repo import CatchupRepo
    from ..domain import catchup as policy
    from ..jobs.catchup import reconcile_plan

    plan = await CatchupRepo(session).get(chat_id, user_id)
    if plan is None:
        return False
    queue = policy.drop_pages(plan.queue, task.page_start, task.page_end)
    if queue == [list(r) for r in plan.queue]:
        return False
    plan.queue = queue
    # A group reading may supersede all or part of the piece already sent.
    # Retire its button; unread pages will be offered as a fresh piece.
    if plan.pending_start is not None and (
        plan.pending_start <= task.page_end and task.page_start <= plan.pending_end
    ):
        plan.pending_start = None
        plan.pending_end = None
        plan.pending_message_id = None
        plan.pending_token += 1
    if queue:
        plan.cursor = queue[0][0]
    return await reconcile_plan(session, plan, display_name=display_name)


async def on_done_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_chat is None:
        return

    task_id = int(query.data.split(":", 1)[1])
    text, _ = await record_completion(
        get_deps(context),
        context.bot,
        update.effective_chat.id,
        query.from_user.id,
        display_name=query.from_user.full_name,
        task_id=task_id,
    )
    await query.answer(text[:ANSWER_LIMIT])


async def on_who_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the finished/pending split privately, without posting to the group."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_chat is None:
        return

    task_id = int(query.data.split(":", 1)[1])
    deps = get_deps(context)

    async with session_scope(deps.sessions) as session:
        tasks = TaskRepo(session)
        task = await tasks.get(task_id)
        if task is None or task.chat_id != update.effective_chat.id:
            await query.answer(ar.NO_OPEN_WIRD[:ANSWER_LIMIT], show_alert=True)
            return
        view = await build_view(session, task)
        pending = [s.display_name for s in await tasks.pending_subscribers(task.id)]

    lines = [f"أنجز {render.ar_num(view.done_count)} من {render.ar_num(view.subscriber_count)}"]
    if view.done_names:
        lines.append("✅ " + "، ".join(view.done_names))
    if pending:
        lines.append("⏳ " + "، ".join(pending))

    await query.answer("\n".join(lines)[:ANSWER_LIMIT], show_alert=True)


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Text equivalent of the button, for members who prefer typing."""
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or user is None or message is None:
        return

    text, _ = await record_completion(
        get_deps(context),
        context.bot,
        chat.id,
        user.id,
        display_name=user.full_name,
        source=CompletionSource.COMMAND,
    )
    await message.reply_text(text)


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(on_done_button, pattern=rf"^{render.CB_DONE}:\d+$"))
    app.add_handler(CallbackQueryHandler(on_who_button, pattern=rf"^{render.CB_WHO}:\d+$"))
    app.add_handler(CommandHandler("done", done_command, filters=filters.ChatType.GROUPS))
