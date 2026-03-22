"""Tests for scriptbox.main — entrypoint that runs bot + scheduler."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Keys that conftest.py may have loaded into os.environ from .env.
_SCRIPTBOX_ENV_KEYS = (
    "SCRIPTBOX_BOT_TOKEN",
    "SCRIPTBOX_CHAT_ID",
    "SCRIPTBOX_SCRIPTS_DIR",
    "SCRIPTBOX_DB_PATH",
    "SCRIPTBOX_USE_SANDBOX",
)


def _clear_scriptbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _SCRIPTBOX_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_env(tmp_path: Path, **overrides: str) -> str:
    defaults = {
        "SCRIPTBOX_BOT_TOKEN": "123:FAKE",
        "SCRIPTBOX_CHAT_ID": "42",
        "SCRIPTBOX_SCRIPTS_DIR": str(tmp_path / "scripts"),
        "SCRIPTBOX_DB_PATH": str(tmp_path / "test.db"),
        "SCRIPTBOX_USE_SANDBOX": "false",
    }
    defaults.update(overrides)
    p = tmp_path / ".env"
    p.write_text("\n".join(f"{k}={v}" for k, v in defaults.items()) + "\n")
    return str(p)


def _setup_scripts_dir(tmp_path: Path) -> None:
    """Create a minimal scripts directory so the loader doesn't complain."""
    d = tmp_path / "scripts"
    d.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestConfigLoading:
    async def test_loads_from_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _clear_scriptbox_env(monkeypatch)
        _setup_scripts_dir(tmp_path)
        env_path = _write_env(tmp_path)

        with patch("scriptbox.main.ScriptBoxBot") as MockBot, \
             patch("scriptbox.main.TelegramNotifier") as MockNotifier:
            mock_bot = MockBot.return_value
            mock_bot.start = AsyncMock()
            mock_bot.stop = AsyncMock()

            MockNotifier.return_value.send = AsyncMock()

            stop = asyncio.Event()
            stop.set()  # Exit immediately

            from scriptbox.main import run
            await run(env_path, _stop_event=stop)

            # Bot was constructed with the config from the .env file
            MockBot.assert_called_once()
            runner_arg = MockBot.call_args[0][0]
            assert runner_arg is not None

    async def test_loads_from_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        _clear_scriptbox_env(monkeypatch)
        _setup_scripts_dir(tmp_path)

        # Write a minimal .env (config still reads the file for secrets)
        env_path = _write_env(tmp_path)

        # Override via env vars — these take precedence
        monkeypatch.setenv("SCRIPTBOX_BOT_TOKEN", "999:ENVTOKEN")
        monkeypatch.setenv("SCRIPTBOX_CHAT_ID", "77")

        with patch("scriptbox.main.ScriptBoxBot") as MockBot, \
             patch("scriptbox.main.TelegramNotifier") as MockNotifier:
            mock_bot = MockBot.return_value
            mock_bot.start = AsyncMock()
            mock_bot.stop = AsyncMock()

            MockNotifier.return_value.send = AsyncMock()

            stop = asyncio.Event()
            stop.set()

            from scriptbox.main import run
            await run(env_path, _stop_event=stop)

            # TelegramNotifier gets the env-var token, not the file token
            MockNotifier.assert_called_once_with("999:ENVTOKEN", 77)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    async def test_stop_event_triggers_teardown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Setting the stop event causes bot.stop() and scheduler shutdown."""
        _clear_scriptbox_env(monkeypatch)
        _setup_scripts_dir(tmp_path)
        env_path = _write_env(tmp_path)

        with patch("scriptbox.main.ScriptBoxBot") as MockBot, \
             patch("scriptbox.main.TelegramNotifier") as MockNotifier:
            mock_bot = MockBot.return_value
            mock_bot.start = AsyncMock()
            mock_bot.stop = AsyncMock()

            MockNotifier.return_value.send = AsyncMock()

            stop = asyncio.Event()

            from scriptbox.main import run

            task = asyncio.create_task(run(env_path, _stop_event=stop))

            # Let it start
            await asyncio.sleep(0.05)

            # Trigger shutdown
            stop.set()

            # Should finish within 5 seconds
            await asyncio.wait_for(task, timeout=5)

            mock_bot.stop.assert_awaited_once()

    async def test_exits_cleanly_within_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The main loop exits cleanly when the stop event is set."""
        _clear_scriptbox_env(monkeypatch)
        _setup_scripts_dir(tmp_path)
        env_path = _write_env(tmp_path)

        with patch("scriptbox.main.ScriptBoxBot") as MockBot, \
             patch("scriptbox.main.TelegramNotifier") as MockNotifier:
            mock_bot = MockBot.return_value
            mock_bot.start = AsyncMock()
            mock_bot.stop = AsyncMock()

            MockNotifier.return_value.send = AsyncMock()

            stop = asyncio.Event()

            from scriptbox.main import run

            task = asyncio.create_task(run(env_path, _stop_event=stop))
            await asyncio.sleep(0.05)
            stop.set()

            # Must complete — no hanging
            done, pending = await asyncio.wait({task}, timeout=5)
            assert task in done
            assert not pending


# ---------------------------------------------------------------------------
# Runner notifier integration
# ---------------------------------------------------------------------------


class TestRunnerNotifier:
    async def test_notifier_set_on_runner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """run() should call runner.set_notifier() with a TelegramNotifier."""
        _clear_scriptbox_env(monkeypatch)
        _setup_scripts_dir(tmp_path)
        env_path = _write_env(tmp_path)

        with patch("scriptbox.main.ScriptBoxBot") as MockBot, \
             patch("scriptbox.main.TelegramNotifier") as MockNotifier, \
             patch("scriptbox.main.Runner") as MockRunner:
            mock_runner = MockRunner.return_value
            mock_runner.setup = AsyncMock()
            mock_runner.scheduler = MagicMock()
            mock_runner.scheduler.running = False

            mock_bot = MockBot.return_value
            mock_bot.start = AsyncMock()
            mock_bot.stop = AsyncMock()

            stop = asyncio.Event()
            stop.set()

            from scriptbox.main import run
            await run(env_path, _stop_event=stop)

            mock_runner.set_notifier.assert_called_once_with(MockNotifier.return_value)
