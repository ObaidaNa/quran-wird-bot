"""Daily wird tasks, completions, and the reminder log."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Completion,
    CompletionSource,
    DailyTask,
    ReminderLog,
    Subscriber,
    TaskStatus,
    utcnow,
)


class TaskRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        chat_id: int,
        *,
        task_date: dt.date,
        page_start: int,
        page_end: int,
        is_repeat_of: int | None = None,
    ) -> DailyTask:
        task = DailyTask(
            chat_id=chat_id,
            task_date=task_date,
            page_start=page_start,
            page_end=page_end,
            is_repeat_of=is_repeat_of,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get(self, task_id: int) -> DailyTask | None:
        return await self.session.get(DailyTask, task_id)

    async def get_by_date(self, chat_id: int, task_date: dt.date) -> DailyTask | None:
        return await self.session.scalar(
            select(DailyTask).where(DailyTask.chat_id == chat_id, DailyTask.task_date == task_date)
        )

    async def get_open(self, chat_id: int) -> DailyTask | None:
        """The group's currently open wird, if any."""
        return await self.session.scalar(
            select(DailyTask)
            .where(DailyTask.chat_id == chat_id, DailyTask.status == TaskStatus.OPEN)
            .order_by(DailyTask.task_date.desc())
        )

    async def list_open(self) -> Sequence[DailyTask]:
        result = await self.session.scalars(
            select(DailyTask).where(DailyTask.status == TaskStatus.OPEN)
        )
        return result.all()

    async def set_messages(
        self, task_id: int, *, message_id: int | None, album_message_ids: list[int] | None
    ) -> None:
        task = await self.session.get(DailyTask, task_id)
        if task is not None:
            task.message_id = message_id
            task.album_message_ids = album_message_ids

    async def close(self, task_id: int) -> DailyTask | None:
        task = await self.session.get(DailyTask, task_id)
        if task is not None and task.status is not TaskStatus.CLOSED:
            task.status = TaskStatus.CLOSED
            task.closed_at = utcnow()
        return task

    async def list_between(self, chat_id: int, start: dt.date, end: dt.date) -> Sequence[DailyTask]:
        """Tasks in an inclusive date window, oldest first — used by the weekly report."""
        result = await self.session.scalars(
            select(DailyTask)
            .where(
                DailyTask.chat_id == chat_id,
                DailyTask.task_date >= start,
                DailyTask.task_date <= end,
            )
            .order_by(DailyTask.task_date)
        )
        return result.all()

    # ---------------------------------------------------------------- completions

    async def mark_done(
        self,
        task_id: int,
        user_id: int,
        *,
        was_subscriber: bool,
        source: CompletionSource = CompletionSource.BUTTON,
    ) -> bool:
        """Record a completion. Returns False if it was already recorded.

        Pressing the button twice must not count twice, so this is idempotent.
        """
        existing = await self.session.get(Completion, (task_id, user_id))
        if existing is not None:
            return False
        self.session.add(
            Completion(
                task_id=task_id,
                user_id=user_id,
                was_subscriber=was_subscriber,
                source=source,
            )
        )
        await self.session.flush()
        return True

    async def undo_done(self, task_id: int, user_id: int) -> bool:
        existing = await self.session.get(Completion, (task_id, user_id))
        if existing is None:
            return False
        await self.session.delete(existing)
        # Flush so a later get() in the same session does not resurrect the row
        # from the identity map.
        await self.session.flush()
        return True

    async def has_done(self, task_id: int, user_id: int) -> bool:
        return await self.session.get(Completion, (task_id, user_id)) is not None

    async def done_user_ids(self, task_id: int) -> list[int]:
        result = await self.session.scalars(
            select(Completion.user_id)
            .where(Completion.task_id == task_id)
            .order_by(Completion.done_at)
        )
        return list(result.all())

    async def done_count(self, task_id: int) -> int:
        return (
            await self.session.scalar(
                select(func.count()).select_from(Completion).where(Completion.task_id == task_id)
            )
            or 0
        )

    async def pending_subscribers(self, task_id: int) -> Sequence[Subscriber]:
        """Active subscribers who have not marked this wird done yet."""
        task = await self.session.get(DailyTask, task_id)
        if task is None:
            return []
        done = select(Completion.user_id).where(Completion.task_id == task_id)
        result = await self.session.scalars(
            select(Subscriber)
            .where(
                Subscriber.chat_id == task.chat_id,
                Subscriber.is_active.is_(True),
                Subscriber.user_id.not_in(done),
            )
            .order_by(Subscriber.joined_at)
        )
        return result.all()

    # ------------------------------------------------------------------ reminders

    async def reminder_sent(self, task_id: int, seq: int) -> bool:
        return await self.session.get(ReminderLog, (task_id, seq)) is not None

    async def log_reminder(
        self, task_id: int, seq: int, *, message_ids: list[int] | None = None
    ) -> bool:
        """Record a sent reminder. Returns False if this sequence already went out.

        A restart re-schedules jobs from the database, so this guard is what stops
        members being pinged twice for the same reminder.
        """
        if await self.reminder_sent(task_id, seq):
            return False
        self.session.add(ReminderLog(task_id=task_id, seq=seq, message_ids=message_ids))
        await self.session.flush()
        return True
