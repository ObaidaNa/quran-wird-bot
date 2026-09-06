"""Assembling the application.

Importing `app` is not enough to prove it works: `build_application` takes a
parameter named `settings`, which once shadowed the `settings` handler module
and turned its registration into a call on the Settings object — an
AttributeError at startup that no import and no linter caught. Building the
application for real is what catches that class of mistake.

Nothing here touches the network; ApplicationBuilder only validates the token's
shape.
"""

from __future__ import annotations

from telegram.ext import CallbackQueryHandler, CommandHandler

from quran_wird.app import build_application
from quran_wird.config import Settings
from quran_wird.messages import ar

# Every command the bot answers, including the ones only admins may use.
EXPECTED_COMMANDS = {
    "start",
    "help",
    "join",
    "leave",
    "done",
    "today",
    "sendnow",
    "me",
    "progress",
    "top",
    "week",
    "settings",
    "setpage",
}


def build(tmp_path):
    return build_application(
        Settings(
            bot_token="123:FAKE",
            db_path=tmp_path / "bot.db",
            pages_dir=tmp_path / "pages",
        )
    )


def registered_commands(app) -> set[str]:
    found: set[str] = set()
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                found.update(handler.commands)
    return found


def test_every_command_is_registered(tmp_path):
    app = build(tmp_path)
    assert EXPECTED_COMMANDS <= registered_commands(app)


def test_the_help_text_promises_nothing_unregistered(tmp_path):
    # A command advertised in /help but never wired up is a dead end for a
    # member who types it.
    commands = registered_commands(build(tmp_path))
    promised = {word.lstrip("/").strip() for word in ar.HELP.split() if word.startswith("/")}
    assert promised <= commands


def test_the_botfather_menu_matches_real_commands(tmp_path):
    commands = registered_commands(build(tmp_path))
    assert {name for name, _ in ar.COMMAND_DESCRIPTIONS} <= commands


def test_the_buttons_are_registered(tmp_path):
    app = build(tmp_path)
    patterns = [
        h.pattern.pattern
        for group in app.handlers.values()
        for h in group
        if isinstance(h, CallbackQueryHandler) and h.pattern is not None
    ]
    # The wird's two buttons and the settings panel.
    assert any("done" in p for p in patterns)
    assert any("who" in p for p in patterns)
    assert any("cfg" in p for p in patterns)
