"""Application assembly: dependencies, handlers, and the run mode."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder, Defaults

from .config import Settings
from .db.session import create_engine_async, session_factory
from .deps import DEPS_KEY, Deps
from .handlers import errors, lifecycle, mark_done, subscribe, wird
from .jobs.scheduler import reschedule_all
from .messages import ar

log = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    """Publish the command menu and rebuild every group's jobs."""
    await app.bot.set_my_commands(
        [BotCommand(name, description) for name, description in ar.COMMAND_DESCRIPTIONS]
    )
    me = await app.bot.get_me()
    log.info("connected as @%s (%s)", me.username, me.id)

    # Jobs are in-memory only, so they have to be rebuilt from the database on
    # every start.
    await reschedule_all(app)


async def _post_shutdown(app: Application) -> None:
    deps: Deps = app.bot_data[DEPS_KEY]
    await deps.engine.dispose()
    log.info("database connections closed")


def build_application(settings: Settings) -> Application:
    engine = create_engine_async(settings.db_path)
    deps = Deps(settings=settings, engine=engine, sessions=session_factory(engine))

    defaults = Defaults(
        parse_mode=ParseMode.HTML,
        tzinfo=ZoneInfo(settings.default_timezone),
        link_preview_options=None,
    )

    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .defaults(defaults)
        # Telegram throttles group messages; the reminder fan-out would trip it.
        .rate_limiter(AIORateLimiter())
        .connect_timeout(settings.connect_timeout)
        .read_timeout(settings.read_timeout)
        .write_timeout(settings.read_timeout)
        .get_updates_connect_timeout(settings.connect_timeout)
        .get_updates_read_timeout(settings.read_timeout)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data[DEPS_KEY] = deps

    lifecycle.register(app)
    subscribe.register(app)
    wird.register(app)
    mark_done.register(app)
    settings.register(app)
    app.add_error_handler(errors.on_error)
    return app


def run(app: Application, settings: Settings) -> None:
    """Start the bot in whichever mode the environment selects."""
    # chat_member is not in the default allowed_updates, and without it the bot
    # never learns that a member left; callback_query is needed for the wird button.
    allowed = Update.ALL_TYPES

    if settings.use_webhook:
        log.info("starting in webhook mode on port %s", settings.port)
        app.run_webhook(
            listen="0.0.0.0",
            port=settings.port,
            url_path=settings.effective_url_path,
            secret_token=settings.secret_token or None,
            webhook_url=settings.full_webhook_url,
            allowed_updates=allowed,
            # Keep updates that arrived while the bot was restarting, so a button
            # press during a deploy is not silently lost.
            drop_pending_updates=False,
        )
    else:
        log.info("starting in long polling mode")
        app.run_polling(allowed_updates=allowed, drop_pending_updates=False)
