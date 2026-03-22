"""Tests for scriptbox.telegram.handlers — PTB command handlers."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scriptbox.executor import ExecutionResult
from scriptbox.loader import ScriptInfo
from scriptbox.telegram.handlers import (
    _PAUSED_FILE,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _dummy_script(id: str, name: str | None = None, meta: dict | None = None) -> ScriptInfo:
    """Create a minimal ScriptInfo for testing."""
    return ScriptInfo(
        id=id,
        path=Path(f"/scripts/{id}.py"),
        name=name or id.replace("_", " ").title(),
        meta=meta or {},
        run_fn=AsyncMock(),
    )


def _make_update() -> MagicMock:
    """Build a minimal mock that looks like a python-telegram-bot Update."""
    update = MagicMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def _make_context(
    runner: MagicMock,
    args: list[str] | None = None,
) -> MagicMock:
    """Build a minimal mock that looks like a PTB CallbackContext."""
    context = MagicMock()
    context.bot_data = {"runner": runner}
    context.args = args or []
    return context


def _make_runner(
    scripts: list[ScriptInfo] | None = None,
    dag: dict[str, list[str]] | None = None,
) -> MagicMock:
    runner = MagicMock()
    runner.get_scripts.return_value = scripts or []
    runner.get_dag.return_value = dag or {}
    runner.trigger = AsyncMock(return_value=[])
    runner.run_logger = MagicMock()
    runner.run_logger.get_runs = AsyncMock(return_value=[])
    runner.run_logger.get_stats = AsyncMock(return_value={})
    runner.scheduler = MagicMock()
    return runner


# ---------------------------------------------------------------------------
# /scripts
# ---------------------------------------------------------------------------


class TestCmdScripts:
    async def test_lists_scripts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        scripts = [
            _dummy_script("fetch", meta={"schedule": "*/5 * * * *"}),
            _dummy_script("report"),
        ]
        runner = _make_runner(scripts=scripts)
        update = _make_update()
        context = _make_context(runner)

        monkeypatch.setattr("scriptbox.telegram.handlers._PAUSED_FILE", tmp_path / "paused.json")
        await cmd_scripts(update, context)

        update.effective_message.reply_text.assert_awaited_once()
        text = update.effective_message.reply_text.call_args[0][0]
        assert "fetch" in text.lower() or "Fetch" in text
        assert "report" in text.lower() or "Report" in text

    async def test_no_scripts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        runner = _make_runner(scripts=[])
        update = _make_update()
        context = _make_context(runner)

        monkeypatch.setattr("scriptbox.telegram.handlers._PAUSED_FILE", tmp_path / "paused.json")
        await cmd_scripts(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "no scripts" in text.lower()


# ---------------------------------------------------------------------------
# /run
# ---------------------------------------------------------------------------


class TestCmdRun:
    async def test_missing_arg(self):
        runner = _make_runner()
        update = _make_update()
        context = _make_context(runner, args=[])

        await cmd_run(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "usage" in text.lower() or "script_id" in text.lower()

    async def test_nonexistent_script(self):
        scripts = [_dummy_script("fetch")]
        runner = _make_runner(scripts=scripts)
        update = _make_update()
        context = _make_context(runner, args=["nope"])

        await cmd_run(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "not found" in text.lower()

    async def test_valid_script(self):
        scripts = [_dummy_script("fetch")]
        results = [ExecutionResult("fetch", "success", 150, None, {"data": 1})]
        runner = _make_runner(scripts=scripts)
        runner.trigger.return_value = results
        update = _make_update()
        context = _make_context(runner, args=["fetch"])

        await cmd_run(update, context)

        runner.trigger.assert_awaited_once_with("fetch")
        # First call: "⏳ Running...", second call: results
        assert update.effective_message.reply_text.await_count == 2
        final_text = update.effective_message.reply_text.call_args_list[-1][0][0]
        assert "fetch" in final_text


# ---------------------------------------------------------------------------
# /logs
# ---------------------------------------------------------------------------


class TestCmdLogs:
    async def test_missing_arg(self):
        runner = _make_runner()
        update = _make_update()
        context = _make_context(runner, args=[])

        await cmd_logs(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "usage" in text.lower() or "script_id" in text.lower()

    async def test_valid_script(self):
        runs = [
            {"script_id": "fetch", "started_at": "2026-03-20T10:00:00", "status": "success", "duration_ms": 200},
        ]
        runner = _make_runner()
        runner.run_logger.get_runs.return_value = runs
        update = _make_update()
        context = _make_context(runner, args=["fetch"])

        await cmd_logs(update, context)

        runner.run_logger.get_runs.assert_awaited_once()
        text = update.effective_message.reply_text.call_args[0][0]
        assert "fetch" in text

    async def test_custom_limit(self):
        runner = _make_runner()
        runner.run_logger.get_runs.return_value = []
        update = _make_update()
        context = _make_context(runner, args=["fetch", "10"])

        await cmd_logs(update, context)

        call_kwargs = runner.run_logger.get_runs.call_args
        assert call_kwargs[1].get("limit") == 10 or call_kwargs.kwargs.get("limit") == 10


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


class TestCmdStatus:
    async def test_status_reply(self):
        scripts = [_dummy_script("fetch", meta={"schedule": "*/5 * * * *"})]
        runner = _make_runner(scripts=scripts)
        update = _make_update()
        context = _make_context(runner)

        with patch("scriptbox.telegram.handlers.docker", create=True) as mock_docker:
            mock_docker.from_env.return_value.ping.side_effect = Exception("no docker")
            await cmd_status(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "status" in text.lower() or "ScriptBox" in text


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------


class TestCmdStats:
    async def test_aggregate_stats(self):
        stats = {"total_runs": 10, "success_count": 8, "failure_count": 2, "success_rate": 0.8, "avg_duration_ms": 500}
        runner = _make_runner()
        runner.run_logger.get_stats.return_value = stats
        update = _make_update()
        context = _make_context(runner, args=[])

        await cmd_stats(update, context)

        runner.run_logger.get_stats.assert_awaited_once_with(script_id=None)
        text = update.effective_message.reply_text.call_args[0][0]
        assert "10" in text or "Stats" in text

    async def test_per_script_stats(self):
        stats = {"total_runs": 5, "success_count": 5, "failure_count": 0, "success_rate": 1.0, "avg_duration_ms": 100}
        runner = _make_runner()
        runner.run_logger.get_stats.return_value = stats
        update = _make_update()
        context = _make_context(runner, args=["fetch"])

        await cmd_stats(update, context)

        runner.run_logger.get_stats.assert_awaited_once_with(script_id="fetch")


# ---------------------------------------------------------------------------
# /pause + /resume
# ---------------------------------------------------------------------------


class TestCmdPauseResume:
    async def test_pause_missing_arg(self):
        runner = _make_runner()
        update = _make_update()
        context = _make_context(runner, args=[])

        await cmd_pause(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "usage" in text.lower() or "script_id" in text.lower()

    async def test_pause_nonexistent_script(self):
        scripts = [_dummy_script("fetch")]
        runner = _make_runner(scripts=scripts)
        update = _make_update()
        context = _make_context(runner, args=["nope"])

        await cmd_pause(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "not found" in text.lower()

    async def test_pause_and_resume(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        paused_file = tmp_path / "paused.json"
        monkeypatch.setattr("scriptbox.telegram.handlers._PAUSED_FILE", paused_file)

        scripts = [_dummy_script("fetch", meta={"schedule": "*/5 * * * *"})]
        runner = _make_runner(scripts=scripts)

        # Pause
        update = _make_update()
        context = _make_context(runner, args=["fetch"])
        await cmd_pause(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "paused" in text.lower()
        assert paused_file.exists()
        assert "fetch" in json.loads(paused_file.read_text())

        # Pause again — already paused
        update2 = _make_update()
        context2 = _make_context(runner, args=["fetch"])
        await cmd_pause(update2, context2)
        text2 = update2.effective_message.reply_text.call_args[0][0]
        assert "already paused" in text2.lower()

        # Resume
        update3 = _make_update()
        context3 = _make_context(runner, args=["fetch"])
        await cmd_resume(update3, context3)

        text3 = update3.effective_message.reply_text.call_args[0][0]
        assert "resumed" in text3.lower()
        assert "fetch" not in json.loads(paused_file.read_text())

    async def test_resume_not_paused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("scriptbox.telegram.handlers._PAUSED_FILE", tmp_path / "paused.json")

        runner = _make_runner()
        update = _make_update()
        context = _make_context(runner, args=["fetch"])

        await cmd_resume(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "not paused" in text.lower()


# ---------------------------------------------------------------------------
# /graph
# ---------------------------------------------------------------------------


class TestCmdGraph:
    async def test_graph_full(self):
        dag = {"fetch": [], "process": ["fetch"], "send": ["process"]}
        runner = _make_runner(dag=dag)
        update = _make_update()
        context = _make_context(runner, args=[])

        await cmd_graph(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "fetch" in text
        assert "→" in text or "process" in text

    async def test_graph_single_target(self):
        dag = {"fetch": [], "process": ["fetch"], "send": ["process"]}
        runner = _make_runner(dag=dag)
        update = _make_update()
        context = _make_context(runner, args=["send"])

        await cmd_graph(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "send" in text

    async def test_graph_empty(self):
        runner = _make_runner(dag={})
        update = _make_update()
        context = _make_context(runner, args=[])

        await cmd_graph(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "no scripts" in text.lower()


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


class TestCmdHelp:
    async def test_help_reply(self):
        update = _make_update()
        context = MagicMock()

        await cmd_help(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "/scripts" in text
        assert "/run" in text
        assert "/help" in text


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_runner_exception_sends_error(self):
        """If the runner raises, the handler catches it and sends an error message."""
        runner = _make_runner()
        runner.get_scripts.side_effect = RuntimeError("boom")
        update = _make_update()
        context = _make_context(runner)

        await cmd_scripts(update, context)

        text = update.effective_message.reply_text.call_args[0][0]
        assert "error" in text.lower() or "❌" in text
