"""Tests for scriptbox.telegram.notifier — TelegramNotifier and SandboxNotifier."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from scriptbox.telegram.notifier import (
    SandboxNotifier,
    TelegramNotifier,
    _split_message,
)


# ---------------------------------------------------------------------------
# SandboxNotifier
# ---------------------------------------------------------------------------


class TestSandboxNotifier:
    async def test_send_appends(self):
        n = SandboxNotifier()
        await n.send("hello")
        await n.send("world", parse_mode="Markdown")
        assert len(n.messages) == 2
        assert n.messages[0] == {"text": "hello", "parse_mode": "HTML"}
        assert n.messages[1] == {"text": "world", "parse_mode": "Markdown"}

    async def test_ask_raises(self):
        n = SandboxNotifier()
        with pytest.raises(NotImplementedError, match="sandbox"):
            await n.ask("proceed?")

    def test_to_json(self):
        n = SandboxNotifier()
        # Synchronous append for setup
        n.messages.append({"text": "hi", "parse_mode": "HTML"})
        raw = n.to_json()
        assert json.loads(raw) == [{"text": "hi", "parse_mode": "HTML"}]

    def test_from_json(self):
        payload = json.dumps([{"text": "a", "parse_mode": "HTML"}])
        msgs = SandboxNotifier.from_json(payload)
        assert msgs == [{"text": "a", "parse_mode": "HTML"}]

    def test_roundtrip(self):
        n = SandboxNotifier()
        n.messages.append({"text": "x", "parse_mode": "HTML"})
        n.messages.append({"text": "y", "parse_mode": "Markdown"})
        restored = SandboxNotifier.from_json(n.to_json())
        assert restored == n.messages


# ---------------------------------------------------------------------------
# _split_message
# ---------------------------------------------------------------------------


class TestSplitMessage:
    def test_short_message_not_split(self):
        assert _split_message("hello") == ["hello"]

    def test_long_message_split(self):
        # 5000 chars should produce 2 chunks
        msg = "a" * 5000
        chunks = _split_message(msg)
        assert len(chunks) == 2
        # All content preserved
        assert "".join(chunks) == msg
        assert all(len(c) <= 4096 for c in chunks)

    def test_split_at_newline(self):
        # Build a message where a newline sits just before the 4096 limit
        line = "x" * 100 + "\n"
        msg = line * 50  # 5050 chars
        chunks = _split_message(msg)
        assert len(chunks) >= 2
        assert all(len(c) <= 4096 for c in chunks)


# ---------------------------------------------------------------------------
# TelegramNotifier.send — message splitting
# ---------------------------------------------------------------------------


class TestTelegramNotifierSend:
    async def test_long_message_produces_multiple_sends(self):
        notifier = TelegramNotifier(bot_token="fake:token", chat_id=123)
        msg = "a" * 5000  # > 4096 → 2 chunks

        with patch.object(notifier, "_post_send", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"message_id": 1}
            await notifier.send(msg)

        assert mock_post.await_count == 2

    async def test_short_message_single_send(self):
        notifier = TelegramNotifier(bot_token="fake:token", chat_id=123)

        with patch.object(notifier, "_post_send", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = {"message_id": 1}
            await notifier.send("hello")

        mock_post.assert_awaited_once()


# ---------------------------------------------------------------------------
# TelegramNotifier.send — error handling
# ---------------------------------------------------------------------------


class TestTelegramNotifierErrorHandling:
    async def test_invalid_token_logs_but_no_raise(self, caplog):
        """send() must swallow errors so script execution is not interrupted."""
        notifier = TelegramNotifier(bot_token="invalid:token", chat_id=123)

        # Simulate a network/API failure inside _post_send
        with patch.object(
            notifier,
            "_post_send",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # Should not raise
            await notifier.send("test message")


# ---------------------------------------------------------------------------
# TelegramNotifier.send_results
# ---------------------------------------------------------------------------


class TestTelegramNotifierSendResults:
    async def test_formats_and_sends(self):
        from scriptbox.executor import ExecutionResult

        notifier = TelegramNotifier(bot_token="fake:token", chat_id=123)
        results = [
            ExecutionResult("fetch", "success", 100, None, {}),
        ]

        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            await notifier.send_results(results, "fetch")

        mock_send.assert_awaited_once()
        sent_text = mock_send.call_args[0][0]
        assert "fetch" in sent_text
        assert "✅" in sent_text


# ---------------------------------------------------------------------------
# Harness writes notifications.json
# ---------------------------------------------------------------------------


class TestHarnessNotifications:
    async def test_notifications_written(self, tmp_path):
        """Harness should write notifications.json when the script sends messages."""
        import json
        from pathlib import Path

        from scriptbox.sandbox.harness import run_harness

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        script_src = """\
META = {"name": "Notifier test"}

async def run(ctx):
    await ctx.telegram.send("hello from sandbox")
    return {"ok": True}
"""
        (run_dir / "script.py").write_text(script_src)
        (run_dir / "config.json").write_text(
            json.dumps({"script_id": "notif_test", "db_path": str(tmp_path / "store.db")})
        )

        rc = await run_harness(run_dir)
        assert rc == 0

        notif_path = run_dir / "notifications.json"
        assert notif_path.exists()
        messages = json.loads(notif_path.read_text())
        assert len(messages) == 1
        assert messages[0]["text"] == "hello from sandbox"

    async def test_no_notifications_no_file(self, tmp_path):
        """No notifications.json if script doesn't use telegram."""
        import json
        from pathlib import Path

        from scriptbox.sandbox.harness import run_harness

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        script_src = """\
META = {"name": "Silent"}

async def run(ctx):
    return {"ok": True}
"""
        (run_dir / "script.py").write_text(script_src)
        (run_dir / "config.json").write_text(
            json.dumps({"script_id": "silent", "db_path": str(tmp_path / "store.db")})
        )

        rc = await run_harness(run_dir)
        assert rc == 0
        assert not (run_dir / "notifications.json").exists()


# ---------------------------------------------------------------------------
# Live Telegram tests (require real token, skipped by default)
# ---------------------------------------------------------------------------

telegram_live = pytest.mark.skipif(
    True,
    reason="Set SCRIPTBOX_TEST_BOT_TOKEN and SCRIPTBOX_TEST_CHAT_ID to run live Telegram tests",
)


@telegram_live
class TestTelegramLive:
    """Manual verification tests. To run:

    SCRIPTBOX_TEST_BOT_TOKEN=... SCRIPTBOX_TEST_CHAT_ID=... \\
        pytest tests/test_telegram_notifier.py::TestTelegramLive -v -s
    """

    @pytest.fixture()
    def live_notifier(self):
        import os

        token = os.environ["SCRIPTBOX_TEST_BOT_TOKEN"]
        chat_id = int(os.environ["SCRIPTBOX_TEST_CHAT_ID"])
        return TelegramNotifier(bot_token=token, chat_id=chat_id)

    async def test_send_real_message(self, live_notifier):
        await live_notifier.send("🧪 <b>ScriptBox test message</b>\nThis is an automated test.")

    async def test_ask_real(self, live_notifier):
        result = await live_notifier.ask(
            "🧪 <b>ScriptBox test</b>\nPlease press Yes or No:", timeout=30
        )
        print(f"ask() returned: {result}")
