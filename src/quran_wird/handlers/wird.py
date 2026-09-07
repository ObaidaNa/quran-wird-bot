"""Wird commands: /today for members, /sendnow for admins.

`/sendnow` doubles as the way out of a wird that no longer matches the group's
settings. The bot posts a wird the moment it is added, guessing at page one; an
admin who then runs `/setpage` used to have no way to correct today — only
tomorrow. Now the already-sent wird is offered for replacement instead of being
reported as a flat "already sent".
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

from ..deps import get_deps
from ..jobs.scheduler import clear_task_reminders
from ..jobs.send_daily import (
    current_view,
    replace_wird,
    replaceable_today,
    schedule_reminders_for,
    send_wird,
)
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

    # Today's wird may already be out but no longer be the right one — the group
    # changed its page or its pace after it went. Offer the swap rather than
    # refusing, which is what left a new group stuck until the next morning.
    offer = await replaceable_today(deps, chat.id)
    if offer is not None:
        if not offer.differs:
            await message.reply_text(ar.ALREADY_SENT_TODAY)
            return
        await message.reply_text(
            render.replace_offer(offer),
            reply_markup=render.replace_keyboard(offer.task_id),
        )
        return

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


async def on_replace_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Carry out the swap an admin confirmed under `/sendnow` or `/setpage`."""
    query, chat = update.callback_query, update.effective_chat
    if query is None or query.data is None or chat is None:
        return

    # The offer sits in the group where anyone can reach its button.
    if not await is_group_admin(update):
        await query.answer(ar.ADMIN_ONLY, show_alert=True)
        return

    old_task_id = int(query.data.split(":", 1)[1])
    deps = get_deps(context)
    try:
        task_id = await replace_wird(context.bot, deps, chat.id, old_task_id)
    except PageImageMissing:
        await query.answer()
        await context.bot.send_message(chat.id, ar.PAGES_MISSING)
        return

    if task_id is None:
        await query.answer(ar.REPLACE_GONE, show_alert=True)
        return

    await query.answer(ar.REPLACE_DONE)
    try:
        await query.edit_message_text(ar.REPLACE_DONE_NOTICE)
    except TelegramError:
        log.debug("chat %s: could not rewrite the replacement offer", chat.id)

    if context.job_queue is not None:
        # The withdrawn wird's reminders would find nothing and send nothing,
        # but leaving them queued means the process carries dead jobs all day.
        clear_task_reminders(context.job_queue, chat.id, old_task_id)
        await schedule_reminders_for(deps, context.job_queue, chat.id, task_id)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("today", today, filters=GROUP_FILTER))
    app.add_handler(CommandHandler("sendnow", send_now, filters=GROUP_FILTER))
    app.add_handler(CallbackQueryHandler(on_replace_button, pattern=rf"^{render.CB_REPLACE}:\d+$"))
