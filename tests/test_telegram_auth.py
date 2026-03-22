"""Tests for scriptbox.telegram.auth — config loading and authorization."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scriptbox.telegram.auth import (
    TelegramConfig,
    TelegramConfigError,
    authorized,
    require_auth,
)


# ---------------------------------------------------------------------------
# TelegramConfig.from_file
# ---------------------------------------------------------------------------


class TestFromFile:
    def test_valid_json(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"bot_token": "tok:123", "chat_ids": [111, 222]}))
        cfg = TelegramConfig.from_file(str(p))
        assert cfg.bot_token == "tok:123"
        assert cfg.chat_ids == [111, 222]
        assert cfg.parse_mode == "HTML"

    def test_missing_token_raises(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"chat_ids": [111]}))
        with pytest.raises(TelegramConfigError, match="bot_token"):
            TelegramConfig.from_file(str(p))

    def test_empty_chat_ids_raises(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"bot_token": "tok:123", "chat_ids": []}))
        with pytest.raises(TelegramConfigError, match="chat_ids"):
            TelegramConfig.from_file(str(p))


# ---------------------------------------------------------------------------
# TelegramConfig.from_env
# ---------------------------------------------------------------------------


class TestFromEnv:
    def test_valid_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SCRIPTBOX_BOT_TOKEN", "tok:abc")
        monkeypatch.setenv("SCRIPTBOX_CHAT_IDS", "100,200,300")
        cfg = TelegramConfig.from_env()
        assert cfg.bot_token == "tok:abc"
        assert cfg.chat_ids == [100, 200, 300]

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SCRIPTBOX_BOT_TOKEN", raising=False)
        monkeypatch.setenv("SCRIPTBOX_CHAT_IDS", "100")
        with pytest.raises(TelegramConfigError, match="SCRIPTBOX_BOT_TOKEN"):
            TelegramConfig.from_env()

    def test_missing_chat_ids_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SCRIPTBOX_BOT_TOKEN", "tok:abc")
        monkeypatch.delenv("SCRIPTBOX_CHAT_IDS", raising=False)
        with pytest.raises(TelegramConfigError, match="SCRIPTBOX_CHAT_IDS"):
            TelegramConfig.from_env()


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
