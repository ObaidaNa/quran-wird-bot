"""Shared fixtures: a throwaway database and a fake Telegram bot."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fakes import FakeSessions

from quran_wird.config import Settings
from quran_wird.db.session import create_all_sync, create_engine_async, session_factory
from quran_wird.deps import Deps


@pytest_asyncio.fixture
async def session(tmp_path):
    """An AsyncSession on an empty, file-backed database.

    A file rather than :memory: because every pooled connection to :memory: gets
    its own private database, which makes multi-connection behaviour untestable.
    """
    engine = create_engine_async(tmp_path / "test.db")
    async with engine.begin() as conn:
        await conn.run_sync(create_all_sync)

    factory = session_factory(engine)
    async with factory() as s:
        yield s

    await engine.dispose()


@pytest.fixture
def pages_dir(tmp_path) -> Path:
    d = tmp_path / "pages"
    d.mkdir()
    for page in range(1, 12):
        (d / f"{page:03d}.png").write_bytes(b"\x89PNG fake")
    return d


@pytest.fixture
def deps(session, pages_dir, tmp_path):
    settings = Settings(
        bot_token="123:FAKE",
        db_path=tmp_path / "bot.db",
        pages_dir=pages_dir,
    )
    return Deps(settings=settings, engine=None, sessions=FakeSessions(session))
