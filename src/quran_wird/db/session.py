"""SQLAlchemy engines and sessions: async for the bot, sync for scripts."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",  # readers do not block on the writer
    "PRAGMA foreign_keys=ON",  # SQLite disables FK enforcement per connection by default
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
)


def _apply_pragmas(dbapi_conn: sqlite3.Connection, _record: object) -> None:
    cur = dbapi_conn.cursor()
    for pragma in _PRAGMAS:
        cur.execute(pragma)
    cur.close()


def _url(db_path: Path, *, async_: bool) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    driver = "sqlite+aiosqlite" if async_ else "sqlite"
    return f"{driver}:///{db_path}"


def create_engine_async(db_path: Path, *, echo: bool = False) -> AsyncEngine:
    engine = create_async_engine(_url(db_path, async_=True), echo=echo)
    event.listen(engine.sync_engine, "connect", _apply_pragmas)
    return engine


def create_engine_sync(db_path: Path, *, echo: bool = False) -> Engine:
    from sqlalchemy import create_engine

    engine = create_engine(_url(db_path, async_=False), echo=echo)
    event.listen(engine, "connect", _apply_pragmas)
    return engine


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Session wrapped in a transaction: commit on success, roll back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def sync_session_scope(engine: Engine) -> Iterator[Session]:
    """Sync counterpart, used by the scripts in scripts/."""
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def run_migrations(db_path: Path) -> None:
    """Upgrade the database to the latest Alembic revision (called on bot startup)."""
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _url(db_path, async_=False))
    command.upgrade(cfg, "head")


def create_all_sync(conn: Connection) -> None:
    """Create tables straight from the models. Tests only — production uses Alembic."""
    from . import models  # noqa: F401  registers the models on the metadata
    from .base import Base

    Base.metadata.create_all(conn)
