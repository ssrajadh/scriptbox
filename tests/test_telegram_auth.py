"""Tests for scriptbox.telegram.auth — config loading and authorization."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scriptbox.config import ConfigError
from scriptbox.telegram.auth import (
    TelegramConfig,
    TelegramConfigError,
    authorized,
    require_auth,
)

# Keys that conftest.py may have loaded into os.environ from .env.
_SCRIPTBOX_ENV_KEYS = (
    "SCRIPTBOX_BOT_TOKEN",
    "SCRIPTBOX_CHAT_ID",
    "SCRIPTBOX_SCRIPTS_DIR",
    "SCRIPTBOX_DB_PATH",
    "SCRIPTBOX_USE_SANDBOX",
)


def _clear_scriptbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all SCRIPTBOX_* vars so tests hit the .env file only."""
    for key in _SCRIPTBOX_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# TelegramConfig.from_env
# ---------------------------------------------------------------------------


class TestFromEnv:
    def test_valid_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_scriptbox_env(monkeypatch)
        p = tmp_path / ".env"
        p.write_text("SCRIPTBOX_BOT_TOKEN=tok:abc\nSCRIPTBOX_CHAT_ID=100\n")
        cfg = TelegramConfig.from_env(str(p))
        assert cfg.bot_token == "tok:abc"
        assert cfg.chat_ids == [100]

    def test_env_var_overrides_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_scriptbox_env(monkeypatch)
        p = tmp_path / ".env"
        p.write_text("SCRIPTBOX_BOT_TOKEN=file_tok\nSCRIPTBOX_CHAT_ID=100\n")
        monkeypatch.setenv("SCRIPTBOX_BOT_TOKEN", "env_tok")
        cfg = TelegramConfig.from_env(str(p))
        assert cfg.bot_token == "env_tok"

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_scriptbox_env(monkeypatch)
        p = tmp_path / ".env"
        p.write_text("SCRIPTBOX_CHAT_ID=100\n")
        with pytest.raises(ConfigError, match="SCRIPTBOX_BOT_TOKEN"):
            TelegramConfig.from_env(str(p))

    def test_missing_chat_id_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        _clear_scriptbox_env(monkeypatch)
        p = tmp_path / ".env"
        p.write_text("SCRIPTBOX_BOT_TOKEN=tok:abc\n")
        with pytest.raises(ConfigError, match="SCRIPTBOX_CHAT_ID"):
            TelegramConfig.from_env(str(p))


# ---------------------------------------------------------------------------
# authorized()
# ---------------------------------------------------------------------------


class TestAuthorized:
    @pytest.fixture()
    def config(self) -> TelegramConfig:
        return TelegramConfig(bot_token="t", chat_ids=[10, 20, 30])

    def test_listed_chat_id(self, config: TelegramConfig):
        assert authorized(20, config) is True

    def test_unlisted_chat_id(self, config: TelegramConfig):
        assert authorized(999, config) is False


# ---------------------------------------------------------------------------
# require_auth wrapper
# ---------------------------------------------------------------------------


def _make_update(chat_id: int) -> MagicMock:
    """Build a minimal mock that looks like a python-telegram-bot Update."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message.reply_text = AsyncMock()
    return update


class TestRequireAuth:
    @pytest.fixture()
    def config(self) -> TelegramConfig:
        return TelegramConfig(bot_token="t", chat_ids=[42])

    async def test_authorized_calls_handler(self, config: TelegramConfig):
        handler = AsyncMock(return_value="ok")
        wrapped = require_auth(handler, config)
        update = _make_update(42)

        result = await wrapped(update, "extra")

        handler.assert_awaited_once_with(update, "extra")
        assert result == "ok"
        update.effective_message.reply_text.assert_not_awaited()

    async def test_unauthorized_replies_and_skips(self, config: TelegramConfig):
        handler = AsyncMock()
        wrapped = require_auth(handler, config)
        update = _make_update(999)

        await wrapped(update)

        handler.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once_with("⛔ Not authorized.")
