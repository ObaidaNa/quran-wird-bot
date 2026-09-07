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

    return phrases.done.pick(chat_id), True


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
