"""Upgrade an already populated pre-catch-up database without changing its history."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from alembic import command
from quran_wird.db.base import Base
from quran_wird.db.models import Completion, DailyTask, Group, Subscriber, TaskMiss, UserStats
from quran_wird.db.session import create_engine_sync


def test_upgrade_preserves_history_and_bounds_inferred_legacy_misses(tmp_path):
    path = tmp_path / "seeded.db"
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(cfg, "76be43b4c3f2")

    engine = create_engine_sync(path)
    try:
        with Session(engine) as session:
            session.add(Group(chat_id=-123, current_page=7))
            session.flush()
            session.add(
                Subscriber(
                    chat_id=-123,
                    user_id=7,
                    display_name="Reader",
                    joined_at=dt.datetime(2026, 1, 1),
                )
            )
            session.add(UserStats(chat_id=-123, user_id=7, total_missed=1, total_done=1))
            for day in range(1, 4):
                session.add(
                    DailyTask(
                        id=day,
                        chat_id=-123,
                        task_date=dt.date(2026, 1, day),
                        page_start=day,
                        page_end=day,
                        status="closed",
                    )
                )
            session.flush()
            session.add(Completion(task_id=3, user_id=7, was_subscriber=True))
            session.commit()

        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")  # replay is harmless
        with Session(engine) as session:
            assert [(m.task_id, m.user_id) for m in session.scalars(select(TaskMiss))] == [(2, 7)]
            assert session.get(Group, -123).current_page == 7
            assert session.get(UserStats, (-123, 7)).total_missed == 1
            assert session.get(Completion, (3, 7)) is not None
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()
