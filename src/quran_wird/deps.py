"""Shared runtime dependencies, carried on the Application's bot_data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from telegram.ext import ContextTypes

from .config import Settings

DEPS_KEY = "deps"


@dataclass(slots=True)
class Deps:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> Deps:
    return context.bot_data[DEPS_KEY]
