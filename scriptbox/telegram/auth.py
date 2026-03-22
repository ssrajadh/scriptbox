from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from scriptbox.config import ConfigError, load_config


class TelegramConfigError(ConfigError):
    """Raised when Telegram configuration is missing or invalid."""


@dataclass
class TelegramConfig:
    bot_token: str
    chat_ids: list[int]
    parse_mode: str = "HTML"

    @classmethod
    def from_env(cls, env_path: str = ".env") -> TelegramConfig:
        """Load config from ``.env`` file and ``os.environ``.

        Uses :func:`scriptbox.config.load_config` under the hood.
        ``SCRIPTBOX_CHAT_ID`` may be a single ID or comma-separated.
        """
        cfg = load_config(env_path)
        return cls(bot_token=cfg.bot_token, chat_ids=[cfg.chat_id])


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
