"""Shared runtime dependencies, carried on the Application's bot_data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from telegram.ext import ContextTypes

from .config import Settings

DEPS_KEY = "deps"


@dataclass(slots=True)
class Deps:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    _catchup_locks: dict[tuple[int, int], asyncio.Lock] = field(default_factory=dict, repr=False)

    def catchup_lock(self, chat_id: int, user_id: int) -> asyncio.Lock:
        """Serialize button and scheduler work for one plan within this process."""
        return self._catchup_locks.setdefault((chat_id, user_id), asyncio.Lock())


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.bot_data[DEPS_KEY]
