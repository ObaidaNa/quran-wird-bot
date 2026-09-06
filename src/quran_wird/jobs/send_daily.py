"""Sending the daily wird."""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..content.base import BuildContext
from ..content.quran_pages import provider as quran_pages
from ..db.models import DailyTask, Group, TaskStatus
from ..db.repo import GroupRepo, MediaRepo, MushafRepo, SubscriberRepo, TaskRepo
from ..db.session import session_scope
from ..deps import Deps, get_deps
from ..domain.progress import next_range
from ..domain.schemas import PageRange, WirdView
from ..messages import render
from ..messages.phrases import phrases
from ..tg.media import PageImageMissing, send_pages

log = logging.getLogger(__name__)


def local_today(group: Group) -> dt.date:
    """Today as the group's members see it, not as the server sees it."""
    return dt.datetime.now(ZoneInfo(group.timezone)).date()


def is_active_day(group: Group, day: dt.date) -> bool:
    return day.weekday() in (group.active_weekdays or [])


async def send_wird(
    bot: Bot,
    deps: Deps,
    chat_id: int,
    *,
    force: bool = False,
) -> int | None:
    """Send today's wird to one group. Returns the task id, or None if skipped.

    `force` is used by /sendnow: it bypasses the active-weekday check but still
    refuses to send twice on the same day, so an admin cannot double-post.
    """
    async with session_scope(deps.sessions) as session:
        groups = GroupRepo(session)
        group = await groups.get(chat_id)
        if group is None or not group.is_active:
            return None

        today = local_today(group)
        if not force and not is_active_day(group, today):
            log.debug("chat %s: %s is not an active weekday", chat_id, today)
            return None

        tasks = TaskRepo(session)
        if await tasks.get_by_date(chat_id, today) is not None:
            log.info("chat %s: wird for %s already sent", chat_id, today)
            return None

        # If yesterday's wird is still open, nobody finished it. Repeat the same
        # pages rather than moving on, and mark the new task as the repeat.
        previous = await tasks.get_open(chat_id)
        if previous is not None:
            pages = PageRange(start=previous.page_start, end=previous.page_end)
            repeat_of = previous.id
            await tasks.close(previous.id)
        else:
            pages = next_range(group.current_page, group.pages_per_day)
            repeat_of = None

        juz, surah_names = await MushafRepo(session).range_summary(pages.start, pages.end)
        subscriber_count = await SubscriberRepo(session).count_active(chat_id)

        task = await tasks.create(
            chat_id,
            task_date=today,
            page_start=pages.start,
            page_end=pages.end,
            is_repeat_of=repeat_of,
        )

        block = await quran_pages.build(
            BuildContext(
                session=session,
                group=group,
                task_date=today,
                pages=pages,
                pages_dir=deps.settings.pages_dir,
            )
        )

        view = WirdView(
            task_id=task.id,
            task_date=today,
            pages=pages,
            juz=juz,
            surah_names=surah_names,
            is_repeat=repeat_of is not None,
            subscriber_count=subscriber_count,
        )
        media = MediaRepo(session)

        try:
            album_ids = await send_pages(
                bot,
                chat_id,
                block.photos,
                pages_dir=deps.settings.pages_dir,
                media=media,
                caption=render.album_caption(view),
            )
        except PageImageMissing:
            log.exception("chat %s: page images missing; wird not sent", chat_id)
            raise

        message = await bot.send_message(
            chat_id,
            render.wird_message(view, phrases.send.pick(chat_id)),
            reply_markup=render.wird_keyboard(task.id),
        )
        await tasks.set_messages(
            task.id, message_id=message.message_id, album_message_ids=album_ids
        )
        task_id = task.id

    log.info("chat %s: sent pages %s-%s (task %s)", chat_id, pages.start, pages.end, task_id)
    return task_id


async def job_send_daily(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue entry point, one per group."""
    chat_id = context.job.chat_id
    deps = get_deps(context)
    try:
        task_id = await send_wird(context.bot, deps, chat_id)
    except (TelegramError, PageImageMissing):
        log.exception("chat %s: daily wird failed", chat_id)
        return

    if task_id is not None and context.job_queue is not None:
        await schedule_reminders_for(deps, context.job_queue, chat_id, task_id)


async def schedule_reminders_for(deps: Deps, job_queue, chat_id: int, task_id: int) -> int:
    """Queue this wird's reminders. Returns how many were scheduled."""
    from .remind import schedule_task_reminders

    async with session_scope(deps.sessions) as session:
        group = await GroupRepo(session).get(chat_id)
        task = await TaskRepo(session).get(task_id)
        if group is None or task is None:
            return 0
        return schedule_task_reminders(job_queue, group, task)


async def current_view(deps: Deps, chat_id: int) -> WirdView | None:
    """The view for the group's currently open wird, if there is one."""
    async with session_scope(deps.sessions) as session:
        task = await TaskRepo(session).get_open(chat_id)
        if task is None or task.status is TaskStatus.CLOSED:
            return None
        return await build_view(session, task)


async def build_view(session: AsyncSession, task: DailyTask) -> WirdView:
    """Rebuild a wird view from the database, including who has finished.

    Names come from the subscriber list; someone who pressed the button without
    subscribing is counted but shown under a generic label, since the bot has no
    stored name for them.
    """
    tasks = TaskRepo(session)
    subs = SubscriberRepo(session)
    juz, surah_names = await MushafRepo(session).range_summary(task.page_start, task.page_end)

    done_ids = await tasks.done_user_ids(task.id)
    by_id = {s.user_id: s.display_name for s in await subs.list_active(task.chat_id)}

    return WirdView(
        task_id=task.id,
        task_date=task.task_date,
        pages=PageRange(start=task.page_start, end=task.page_end),
        juz=juz,
        surah_names=surah_names,
        is_repeat=task.is_repeat_of is not None,
        done_names=[by_id.get(uid, "ضيف") for uid in done_ids],
        subscriber_count=len(by_id),
    )
