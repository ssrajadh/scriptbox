"""PTB-based Telegram bot for ScriptBox.

:class:`ScriptBoxBot` builds a ``python-telegram-bot`` :class:`Application`,
registers every command handler from :mod:`~scriptbox.telegram.handlers`
(wrapped with :func:`~scriptbox.telegram.auth.require_auth`), and exposes
``start()`` / ``stop()`` for lifecycle management.
"""
from __future__ import annotations

import logging
from typing import Any

from telegram.ext import Application, ApplicationBuilder, CommandHandler

from scriptbox.runner import Runner
from scriptbox.telegram.auth import TelegramConfig, require_auth
from scriptbox.telegram.handlers import (
    cmd_graph,
    cmd_help,
    cmd_logs,
    cmd_pause,
    cmd_resume,
    cmd_run,
    cmd_scripts,
    cmd_stats,
    cmd_status,
)

logger = logging.getLogger(__name__)

_COMMANDS: dict[str, Any] = {
    "scripts": cmd_scripts,
    "run": cmd_run,
    "logs": cmd_logs,
    "status": cmd_status,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "stats": cmd_stats,
    "graph": cmd_graph,
    "help": cmd_help,
}


class ScriptBoxBot:
    """Creates and configures a PTB Application for the ScriptBox bot."""

    def __init__(self, runner: Runner, config: TelegramConfig) -> None:
        self._runner = runner
        self._config = config
        self._app: Application | None = None

    def build(self) -> Application:
        """Build and return a fully-configured PTB :class:`Application`.

        * Stores *runner* in ``bot_data["runner"]`` for handler access.
        * Registers every command handler wrapped with auth.
        * Adds an error handler that logs and notifies on errors.
        """
        app: Application = (
            ApplicationBuilder()
            .token(self._config.bot_token)
            .build()
        )

        app.bot_data["runner"] = self._runner

        for name, handler_fn in _COMMANDS.items():
            wrapped = require_auth(handler_fn, self._config)
            app.add_handler(CommandHandler(name, wrapped))

        app.add_error_handler(self._error_handler)

        self._app = app
        return app

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize the application and start polling for updates."""
        if self._app is None:
            self.build()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------

    async def _error_handler(self, update: Any, context: Any) -> None:
        """Log the error and try to notify the user."""
        logger.error("Unhandled bot error: %s", context.error, exc_info=context.error)
        if update and hasattr(update, "effective_message") and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    f"❌ Internal error: {context.error}"
                )
            except Exception:
                pass
