from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine


class TelegramConfigError(Exception):
    """Raised when Telegram configuration is missing or invalid."""


@dataclass
class TelegramConfig:
    bot_token: str
    chat_ids: list[int]
    parse_mode: str = "HTML"

    @classmethod
    def from_file(cls, path: str) -> TelegramConfig:
        """Load config from a JSON file.

        Expected format: ``{"bot_token": "...", "chat_ids": [123456]}``
        """
        data = json.loads(Path(path).read_text())
        token = data.get("bot_token")
        if not token:
            raise TelegramConfigError("bot_token is required")
        chat_ids = data.get("chat_ids")
        if not chat_ids:
            raise TelegramConfigError("chat_ids must be a non-empty list")
        return cls(
            bot_token=token,
            chat_ids=chat_ids,
            parse_mode=data.get("parse_mode", "HTML"),
        )

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """Load config from environment variables.

        Reads ``SCRIPTBOX_BOT_TOKEN`` and ``SCRIPTBOX_CHAT_IDS``
        (comma-separated integers).
        """
        token = os.environ.get("SCRIPTBOX_BOT_TOKEN")
        if not token:
            raise TelegramConfigError("SCRIPTBOX_BOT_TOKEN environment variable is not set")
        raw_ids = os.environ.get("SCRIPTBOX_CHAT_IDS")
        if not raw_ids:
            raise TelegramConfigError("SCRIPTBOX_CHAT_IDS environment variable is not set")
        chat_ids = [int(cid.strip()) for cid in raw_ids.split(",") if cid.strip()]
        if not chat_ids:
            raise TelegramConfigError("SCRIPTBOX_CHAT_IDS must contain at least one ID")
        return cls(bot_token=token, chat_ids=chat_ids)


def authorized(chat_id: int, config: TelegramConfig) -> bool:
    """Return True if *chat_id* is in the config's allowed list."""
    return chat_id in config.chat_ids


def require_auth(
    handler: Callable[..., Coroutine[Any, Any, Any]],
    config: TelegramConfig,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Wrap *handler* so it only runs for authorized chat IDs.

    The wrapper expects the first positional argument (``update``) to
    expose ``update.effective_chat.id`` and
    ``update.effective_message.reply_text()``.  If the chat is not
    authorized it replies with "⛔ Not authorized." and returns
    without calling *handler*.
    """

    async def wrapper(update: Any, *args: Any, **kwargs: Any) -> Any:
        chat_id = update.effective_chat.id
        if not authorized(chat_id, config):
            await update.effective_message.reply_text("⛔ Not authorized.")
            return None
        return await handler(update, *args, **kwargs)

    return wrapper
