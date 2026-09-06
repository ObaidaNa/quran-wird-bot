"""Wird commands: /today for members, /sendnow for admins."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from ..deps import get_deps
from ..jobs.send_daily import current_view, schedule_reminders_for, send_wird
from ..messages import ar, render
from ..messages.phrases import phrases
from ..tg.guards import is_group_admin
from ..tg.media import PageImageMissing

log = logging.getLogger(__name__)

GROUP_FILTER = filters.ChatType.GROUPS


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-post the open wird's status without re-sending the images."""
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return

    view = await current_view(get_deps(context), chat.id)
    if view is None:
        await message.reply_text(ar.NO_OPEN_WIRD)
        return

    await message.reply_text(
        render.wird_message(view, phrases.send.pick(chat.id)),
        reply_markup=render.wird_keyboard(view.task_id),
    )


async def send_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to send today's wird immediately."""
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return

    if not await is_group_admin(update):
        await message.reply_text(ar.ADMIN_ONLY)
        return

    deps = get_deps(context)
    try:
        task_id = await send_wird(context.bot, deps, chat.id, force=True)
    except PageImageMissing:
        await message.reply_text(ar.PAGES_MISSING)
        return

    if task_id is None:
        await message.reply_text(ar.ALREADY_SENT_TODAY)
        return

    # The daily job queues reminders after sending; a wird sent by hand needs
    # them just as much, or nobody is ever reminded about it.
    if context.job_queue is not None:
        await schedule_reminders_for(deps, context.job_queue, chat.id, task_id)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("today", today, filters=GROUP_FILTER))
    app.add_handler(CommandHandler("sendnow", send_now, filters=GROUP_FILTER))
