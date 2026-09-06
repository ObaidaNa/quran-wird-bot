"""Shared fixtures: a throwaway database per test."""

from __future__ import annotations

import pytest_asyncio

from quran_wird.db.session import create_all_sync, create_engine_async, session_factory


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
