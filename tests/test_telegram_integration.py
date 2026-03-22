"""End-to-end integration tests: Telegram command → real Runner → response.

All tests use a real Runner with real scripts on disk (no mocked runner),
but the Telegram API itself is mocked (no real bot needed).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import CommandHandler

from scriptbox.runner import Runner
from scriptbox.telegram.auth import TelegramConfig
from scriptbox.telegram.bot import ScriptBoxBot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHAT_ID = 42
CONFIG = TelegramConfig(bot_token="123:FAKE", chat_ids=[CHAT_ID])

_FETCH_SRC = """\
META = {
    "name": "Fetch",
    "schedule": "0 8 * * *",
}

async def run(ctx):
    return {"data": [1, 2, 3]}
"""

_TRANSFORM_SRC = """\
META = {
    "name": "Transform",
    "depends_on": ["fetch"],
}

async def run(ctx):
    data = ctx.inputs.get("fetch", {}).get("data", [])
    return {"result": sum(data)}
"""

_ALERT_SRC = """\
META = {
    "name": "Alert",
    "depends_on": ["transform"],
}

async def run(ctx):
    result = ctx.inputs.get("transform", {}).get("result", 0)
    await ctx.telegram.send(f"Sum is {result}")
    return {"notified": True}
"""


@pytest.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up scripts dir, Runner, ScriptBoxBot app, and helpers."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "fetch.py").write_text(_FETCH_SRC)
    (scripts_dir / "transform.py").write_text(_TRANSFORM_SRC)
    (scripts_dir / "alert.py").write_text(_ALERT_SRC)

    monkeypatch.setattr(
        "scriptbox.telegram.handlers._PAUSED_FILE", tmp_path / "paused.json"
    )

    db_path = str(tmp_path / "test.db")
    notifier = AsyncMock()
    notifier.send = AsyncMock()
    notifier.send_results = AsyncMock()

    runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
    runner.set_notifier(notifier)
    await runner.setup()
    notifier.send.reset_mock()

    bot = ScriptBoxBot(runner, CONFIG)
    app = bot.build()

    def _handler(name: str) -> CommandHandler:
        return next(
            h for h in app.handlers[0]
            if isinstance(h, CommandHandler) and name in h.commands
        )

    def _call(name: str, args: list[str] | None = None, *, chat_id: int = CHAT_ID):
        """Return (update, context) mocks and invoke the handler."""
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.effective_message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot_data = app.bot_data
        context.args = args or []
        return update, context, _handler(name)

    class Env:
        pass

    e = Env()
    e.runner = runner
    e.notifier = notifier
    e.app = app
    e.call = _call
    e.tmp_path = tmp_path

    yield e

    if runner.scheduler.running:
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reply_texts(update: MagicMock) -> list[str]:
    """Collect all reply_text call texts."""
    return [c.args[0] for c in update.effective_message.reply_text.call_args_list]


# ---------------------------------------------------------------------------
# 1. /scripts — lists all 3 scripts with names and schedules
# ---------------------------------------------------------------------------


class TestScriptsCommand:
    async def test_lists_all_scripts(self, env):
        update, context, handler = env.call("scripts")
        await handler.callback(update, context)

        text = _reply_texts(update)[0]
        assert "Fetch" in text
        assert "Transform" in text
        assert "Alert" in text
        # fetch has schedule "0 8 * * *" → should show schedule info
        assert "8:00" in text or "0 8" in text or "every day" in text
        # transform and alert are manual-only
        assert "manual" in text.lower()


# ---------------------------------------------------------------------------
# 2. /run alert — full DAG chain, "Running..." message, results,
#    and ctx.telegram.send() delivery
# ---------------------------------------------------------------------------


class TestRunFullDAG:
    async def test_run_alert_executes_full_chain(self, env):
        update, context, handler = env.call("run", ["alert"])
        await handler.callback(update, context)

        texts = _reply_texts(update)
        # First reply: "⏳ Running..."
        assert any("Running" in t for t in texts)

        # Second reply: execution results for all 3 scripts
        result_text = texts[-1]
        assert "fetch" in result_text
        assert "transform" in result_text
        assert "alert" in result_text
        # All should succeed
        assert result_text.count("✅") == 3

        # ctx.telegram.send("Sum is 6") should have been forwarded
        # through the notifier (injected as ctx.telegram in local exec).
        calls = [c.args[0] for c in env.notifier.send.call_args_list]
        assert any("Sum is 6" in c for c in calls)


# ---------------------------------------------------------------------------
# 3. /run nonexistent — error
# ---------------------------------------------------------------------------


class TestRunNonexistent:
    async def test_nonexistent_script_error(self, env):
        update, context, handler = env.call("run", ["does_not_exist"])
        await handler.callback(update, context)

        text = _reply_texts(update)[0]
        assert "not found" in text.lower()


# ---------------------------------------------------------------------------
# 4. /logs after multiple runs
# ---------------------------------------------------------------------------


class TestLogsAfterRuns:
    async def test_logs_show_run_history(self, env):
        # Trigger fetch twice to create run history.
        await env.runner.trigger("fetch")
        await env.runner.trigger("fetch")

        update, context, handler = env.call("logs", ["fetch"])
        await handler.callback(update, context)

        text = _reply_texts(update)[0]
        assert "fetch" in text.lower()
        # Should show at least 2 log entries (both successful).
        assert text.count("✅") >= 2


# ---------------------------------------------------------------------------
# 5. /pause and /resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    async def test_pause_and_resume_flow(self, env):
        # Pause fetch
        update1, ctx1, handler1 = env.call("pause", ["fetch"])
        await handler1.callback(update1, ctx1)
        text1 = _reply_texts(update1)[0]
        assert "paused" in text1.lower()

        # The scheduler job should have been removed.
        job_ids = {j.id for j in env.runner.scheduler.get_jobs()}
        assert "fetch" not in job_ids

        # /scripts should show fetch as paused.
        update2, ctx2, handler2 = env.call("scripts")
        await handler2.callback(update2, ctx2)
        scripts_text = _reply_texts(update2)[0]
        assert "⏸" in scripts_text

        # Resume fetch
        update3, ctx3, handler3 = env.call("resume", ["fetch"])
        await handler3.callback(update3, ctx3)
        text3 = _reply_texts(update3)[0]
        assert "resumed" in text3.lower()

        # The scheduler job should be back.
        job_ids = {j.id for j in env.runner.scheduler.get_jobs()}
        assert "fetch" in job_ids


# ---------------------------------------------------------------------------
# 6. /stats — aggregate and per-script
# ---------------------------------------------------------------------------


class TestStats:
    async def test_aggregate_and_per_script_stats(self, env):
        # Create some runs first.
        await env.runner.trigger("fetch")
        await env.runner.trigger("alert")  # runs fetch → transform → alert

        # Aggregate stats
        update1, ctx1, handler = env.call("stats")
        await handler.callback(update1, ctx1)
        agg_text = _reply_texts(update1)[0]
        assert "Stats" in agg_text
        # Should have at least 4 runs total (1 fetch + 3 from alert chain)
        assert "Total runs:" in agg_text or "total" in agg_text.lower()

        # Per-script stats for fetch
        update2, ctx2, _ = env.call("stats", ["fetch"])
        handler2 = env.call("stats", ["fetch"])[2]
        await handler2.callback(update2, ctx2)
        fetch_text = _reply_texts(update2)[0]
        assert "fetch" in fetch_text.lower()
        assert "Runs:" in fetch_text or "runs" in fetch_text.lower()


# ---------------------------------------------------------------------------
# 7. /graph — shows DAG chain
# ---------------------------------------------------------------------------


class TestGraph:
    async def test_graph_shows_chain(self, env):
        update, context, handler = env.call("graph", ["alert"])
        await handler.callback(update, context)

        text = _reply_texts(update)[0]
        # Should show the pipeline: fetch → transform → alert
        assert "fetch" in text
        assert "transform" in text
        assert "alert" in text
        assert "→" in text

    async def test_full_graph(self, env):
        update, context, handler = env.call("graph")
        await handler.callback(update, context)

        text = _reply_texts(update)[0]
        assert "fetch" in text
        assert "→" in text


# ---------------------------------------------------------------------------
# 8. Auth rejection from unauthorized chat_id
# ---------------------------------------------------------------------------


class TestAuthRejection:
    async def test_unauthorized_chat_rejected(self, env):
        update, context, handler = env.call("scripts", chat_id=999)
        await handler.callback(update, context)

        text = _reply_texts(update)[0]
        assert "Not authorized" in text
        assert "⛔" in text


# ---------------------------------------------------------------------------
# 9. Error notification via cron simulation for failing script
# ---------------------------------------------------------------------------


class TestCronErrorNotification:
    async def test_cron_failure_sends_alert(self, env):
        # Add a broken script.
        scripts_dir = env.tmp_path / "scripts"
        (scripts_dir / "broken.py").write_text(
            'META = {"name": "Broken"}\n\n'
            'async def run(ctx):\n'
            '    raise ValueError("something went wrong")\n'
        )
        await env.runner.reload()
        env.notifier.send.reset_mock()

        # Simulate cron firing for the broken script.
        await env.runner._cron_callback("broken")

        calls = [c.args[0] for c in env.notifier.send.call_args_list]
        # Must have a failure alert.
        failure_alerts = [c for c in calls if "Script failed: broken" in c]
        assert len(failure_alerts) == 1
        assert "something went wrong" in failure_alerts[0]
        assert "Duration:" in failure_alerts[0]
        assert "Run ID: #" in failure_alerts[0]

    async def test_cron_success_is_silent(self, env):
        """A successful cron run for a silent script sends nothing."""
        env.notifier.send.reset_mock()

        await env.runner._cron_callback("fetch")

        # Runner should NOT have sent any messages for a successful silent script.
        # (The notifier.send may have been called zero times.)
        calls = [c.args[0] for c in env.notifier.send.call_args_list]
        # No failure alerts, no results summaries.
        assert not any("Script failed" in c for c in calls)
        assert env.notifier.send_results.await_count == 0
