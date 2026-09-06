"""بيئة Alembic — تقرأ النماذج من quran_wird.db.models."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quran_wird.db import models  # noqa: E402,F401  يسجّل الجداول في الـ metadata
from quran_wird.db.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DB_PATH يغلب ما في alembic.ini، ليتطابق السكربت مع إعدادات البوت
db_path = os.environ.get("DB_PATH")
if db_path:
    p = Path(db_path)
    if not p.is_absolute():
        p = ROOT / p
    config.set_main_option("sqlalchemy.url", f"sqlite:///{p}")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite لا يدعم ALTER الكامل — batch mode يعيد بناء الجدول بأمان
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
