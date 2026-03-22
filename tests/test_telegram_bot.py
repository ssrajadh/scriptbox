"""Tests for scriptbox.telegram.bot — ScriptBoxBot and PTB Application wiring."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import CommandHandler

from scriptbox.telegram.auth import TelegramConfig
from scriptbox.telegram.bot import ScriptBoxBot, _COMMANDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.get_scripts.return_value = []
    runner.get_dag.return_value = {}
    runner.trigger = AsyncMock(return_value=[])
    runner.run_logger = MagicMock()
    runner.run_logger.get_runs = AsyncMock(return_value=[])
    runner.run_logger.get_stats = AsyncMock(return_value={})
    runner.scheduler = MagicMock()
    return runner


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


class TestBuild:
    def test_returns_application(self):
        from telegram.ext import Application

        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()
        assert isinstance(app, Application)

    def test_all_commands_registered(self):
        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()

        registered: set[str] = set()
        for handler in app.handlers[0]:
            if isinstance(handler, CommandHandler):
                registered.update(handler.commands)

        expected = {"scripts", "run", "logs", "status", "pause", "resume", "stats", "graph", "help"}
        assert registered == expected

    def test_runner_stored_in_bot_data(self):
        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()
        assert app.bot_data["runner"] is runner

    def test_error_handler_registered(self):
        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()
        assert len(app.error_handlers) > 0

    def test_command_count_matches(self):
        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()
        command_handlers = [h for h in app.handlers[0] if isinstance(h, CommandHandler)]
        assert len(command_handlers) == len(_COMMANDS)


# ---------------------------------------------------------------------------
# Simulated /scripts command
# ---------------------------------------------------------------------------


class TestSimulatedCommand:
    async def test_scripts_command_through_app(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Wire up the application, extract the /scripts handler, call it
        with a mock update, and verify the reply."""
        monkeypatch.setattr(
            "scriptbox.telegram.handlers._PAUSED_FILE", tmp_path / "paused.json"
        )

        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()

        # Find the /scripts command handler
        scripts_handler = next(
            h
            for h in app.handlers[0]
            if isinstance(h, CommandHandler) and "scripts" in h.commands
        )

        # Build a mock update from an *authorized* chat
        update = MagicMock()
        update.effective_chat.id = 42
        update.effective_message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot_data = app.bot_data
        context.args = []

        await scripts_handler.callback(update, context)

        update.effective_message.reply_text.assert_awaited_once()
        text = update.effective_message.reply_text.call_args[0][0]
        assert "no scripts" in text.lower()

    async def test_unauthorized_chat_rejected(self):
        """An unauthorized chat_id must receive the rejection message
        and the real handler must NOT be called."""
        runner = _make_runner()
        config = TelegramConfig(bot_token="123:FAKE", chat_ids=[42])
        bot = ScriptBoxBot(runner, config)
        app = bot.build()

        scripts_handler = next(
            h
            for h in app.handlers[0]
            if isinstance(h, CommandHandler) and "scripts" in h.commands
        )

        update = MagicMock()
        update.effective_chat.id = 999  # NOT in chat_ids
        update.effective_message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot_data = app.bot_data
        context.args = []

        await scripts_handler.callback(update, context)

        update.effective_message.reply_text.assert_awaited_once_with("⛔ Not authorized.")
        # Runner should not have been touched
        runner.get_scripts.assert_not_called()
