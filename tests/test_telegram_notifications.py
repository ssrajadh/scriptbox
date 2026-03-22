"""Tests for Telegram notification flow through Runner, executor, and sandbox."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scriptbox.executor import ExecutionResult
from scriptbox.loader import ScriptInfo
from scriptbox.telegram.notifier import SandboxNotifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dummy_script(
    id: str,
    name: str | None = None,
    meta: dict | None = None,
    run_fn=None,
) -> ScriptInfo:
    return ScriptInfo(
        id=id,
        path=Path(f"/scripts/{id}.py"),
        name=name or id.replace("_", " ").title(),
        meta=meta or {},
        run_fn=run_fn or AsyncMock(return_value={"ok": True}),
    )


def _make_notifier() -> AsyncMock:
    """Build a mock TelegramNotifier."""
    notifier = AsyncMock()
    notifier.send = AsyncMock()
    notifier.send_results = AsyncMock()
    return notifier


# ---------------------------------------------------------------------------
# Runner cron callback — successful script that calls ctx.telegram.send()
# ---------------------------------------------------------------------------


class TestCronSuccessWithTelegram:
    async def test_script_telegram_send_is_forwarded(self, tmp_path: Path):
        """A script that calls ctx.telegram.send() should have its message
        forwarded through the notifier.  The runner itself should NOT send
        an additional results summary."""
        from scriptbox.runner import Runner

        # Script that sends a telegram message.
        async def run_fn(ctx):
            await ctx.telegram.send("hello from script")
            return {"ok": True}

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script_file = scripts_dir / "talker.py"
        script_file.write_text(
            'META = {"name": "Talker"}\n\nasync def run(ctx):\n'
            '    await ctx.telegram.send("hello from script")\n'
            '    return {"ok": True}\n'
        )

        db_path = str(tmp_path / "test.db")
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()

        # Reset after startup message.
        notifier.send.reset_mock()

        await runner._cron_callback("talker")

        # The script's ctx.telegram.send("hello from script") went through
        # the notifier directly (since it's injected as ctx.telegram for
        # local execution).
        calls = [c.args[0] for c in notifier.send.call_args_list]
        assert "hello from script" in calls

        # The runner must NOT have called send_results (no spam).
        notifier.send_results.assert_not_awaited()


# ---------------------------------------------------------------------------
# Runner cron callback — silent successful script
# ---------------------------------------------------------------------------


class TestCronSuccessSilent:
    async def test_silent_script_sends_nothing(self, tmp_path: Path):
        """A successful script that does NOT call ctx.telegram.send()
        should produce zero Telegram messages from the runner."""
        from scriptbox.runner import Runner

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "quiet.py").write_text(
            'META = {"name": "Quiet"}\n\nasync def run(ctx):\n'
            '    return {"ok": True}\n'
        )

        db_path = str(tmp_path / "test.db")
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()
        notifier.send.reset_mock()

        await runner._cron_callback("quiet")

        # Runner must not send anything for a successful silent script.
        notifier.send.assert_not_awaited()
        notifier.send_results.assert_not_awaited()


# ---------------------------------------------------------------------------
# Runner cron callback — failed script
# ---------------------------------------------------------------------------


class TestCronFailure:
    async def test_failure_sends_alert(self, tmp_path: Path):
        """A failed script must always produce a detailed error alert."""
        from scriptbox.runner import Runner

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "broken.py").write_text(
            'META = {"name": "Broken"}\n\nasync def run(ctx):\n'
            '    raise ConnectionError("timeout reaching hn.algolia.com")\n'
        )

        db_path = str(tmp_path / "test.db")
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()
        notifier.send.reset_mock()

        await runner._cron_callback("broken")

        # Must have sent exactly one alert (the failure).
        assert notifier.send.await_count == 1
        alert = notifier.send.call_args_list[0].args[0]
        assert "Script failed: broken" in alert
        assert "timeout reaching hn.algolia.com" in alert
        assert "Duration:" in alert
        assert "Run ID: #" in alert


# ---------------------------------------------------------------------------
# Runner cron callback — DAG partial failure
# ---------------------------------------------------------------------------


class TestCronDAGPartialFailure:
    async def test_partial_failure_sends_summary(self, tmp_path: Path):
        """When a middle script in a DAG fails, the runner should send
        per-failure alerts AND a DAG partial-failure summary."""
        from scriptbox.runner import Runner

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        # fetch → process → send
        (scripts_dir / "fetch.py").write_text(
            'META = {"name": "Fetch"}\n\nasync def run(ctx):\n'
            '    return {"data": [1, 2, 3]}\n'
        )
        (scripts_dir / "process.py").write_text(
            'META = {"name": "Process", "depends_on": ["fetch"]}\n\n'
            'async def run(ctx):\n'
            '    raise RuntimeError("parse error")\n'
        )
        (scripts_dir / "send.py").write_text(
            'META = {"name": "Send", "depends_on": ["process"]}\n\n'
            'async def run(ctx):\n'
            '    return {"sent": True}\n'
        )

        db_path = str(tmp_path / "test.db")
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()
        notifier.send.reset_mock()

        await runner._cron_callback("send")

        calls = [c.args[0] for c in notifier.send.call_args_list]

        # Should have a per-failure alert for "process".
        failure_alerts = [c for c in calls if "Script failed: process" in c]
        assert len(failure_alerts) == 1
        assert "parse error" in failure_alerts[0]

        # Should have a DAG partial failure summary.
        summaries = [c for c in calls if "DAG partial failure" in c]
        assert len(summaries) == 1
        summary = summaries[0]
        assert "✅ fetch" in summary
        assert "❌ process" in summary
        assert "⏭ send" in summary


# ---------------------------------------------------------------------------
# Sandbox notification passthrough (SandboxNotifier → JSON → dispatch)
# ---------------------------------------------------------------------------


class TestSandboxNotificationPassthrough:
    async def test_serialize_deserialize_dispatch(self):
        """SandboxNotifier buffers messages, serializes to JSON, then the
        host side deserializes and dispatches through a real notifier."""
        # --- Container side: script calls ctx.telegram.send() ---
        sandbox = SandboxNotifier()
        await sandbox.send("hello from container")
        await sandbox.send("second message", parse_mode="Markdown")

        payload = sandbox.to_json()

        # --- Host side: DockerExecutor reads notifications.json ---
        messages = SandboxNotifier.from_json(payload)
        assert len(messages) == 2

        # Dispatch through a mock notifier.
        notifier = _make_notifier()
        for msg in messages:
            await notifier.send(
                msg.get("text", ""),
                parse_mode=msg.get("parse_mode", "HTML"),
            )

        assert notifier.send.await_count == 2
        notifier.send.assert_any_await("hello from container", parse_mode="HTML")
        notifier.send.assert_any_await("second message", parse_mode="Markdown")

    async def test_empty_sandbox_no_dispatch(self):
        """A SandboxNotifier with no messages produces an empty list."""
        sandbox = SandboxNotifier()
        assert sandbox.messages == []
        # to_json would give "[]", but the harness only writes the file
        # if there are messages, so from_json("[]") still works.
        assert SandboxNotifier.from_json("[]") == []


# ---------------------------------------------------------------------------
# DockerExecutor notification dispatch
# ---------------------------------------------------------------------------


class TestDockerExecutorDispatch:
    async def test_notifications_json_dispatched(self, tmp_path: Path):
        """DockerExecutor._dispatch_notifications reads the file and
        sends each message through the notifier."""
        from scriptbox.sandbox.docker_executor import DockerExecutor

        notifier = _make_notifier()

        # Can't fully construct DockerExecutor without Docker, so we
        # directly test the _dispatch_notifications method.
        de = object.__new__(DockerExecutor)
        de._notifier = notifier

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "notifications.json").write_text(
            json.dumps([
                {"text": "msg1", "parse_mode": "HTML"},
                {"text": "msg2", "parse_mode": "Markdown"},
            ])
        )

        await de._dispatch_notifications(run_dir)

        assert notifier.send.await_count == 2
        notifier.send.assert_any_await("msg1", parse_mode="HTML")
        notifier.send.assert_any_await("msg2", parse_mode="Markdown")

    async def test_no_notifications_file_is_noop(self, tmp_path: Path):
        """If there's no notifications.json, nothing is dispatched."""
        from scriptbox.sandbox.docker_executor import DockerExecutor

        notifier = _make_notifier()
        de = object.__new__(DockerExecutor)
        de._notifier = notifier

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        await de._dispatch_notifications(run_dir)

        notifier.send.assert_not_awaited()

    async def test_no_notifier_is_noop(self, tmp_path: Path):
        """If no notifier is configured, dispatch is a no-op even with a file."""
        from scriptbox.sandbox.docker_executor import DockerExecutor

        de = object.__new__(DockerExecutor)
        de._notifier = None

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "notifications.json").write_text(
            json.dumps([{"text": "ignored", "parse_mode": "HTML"}])
        )

        await de._dispatch_notifications(run_dir)
        # No error, no send — just a no-op.


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------


class TestDailyDigest:
    async def test_digest_scheduled_when_enabled(self, tmp_path: Path):
        """With daily_digest=True, a digest job is registered on setup."""
        from scriptbox.runner import Runner, _DIGEST_JOB_ID

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        db_path = str(tmp_path / "test.db")

        runner = Runner(
            str(scripts_dir), db_path, use_sandbox=False,
            daily_digest=True, daily_digest_time="09:00",
        )
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()

        job_ids = {j.id for j in runner.scheduler.get_jobs()}
        assert _DIGEST_JOB_ID in job_ids

        runner.scheduler.shutdown(wait=False)

    async def test_digest_not_scheduled_by_default(self, tmp_path: Path):
        from scriptbox.runner import Runner, _DIGEST_JOB_ID

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        db_path = str(tmp_path / "test.db")

        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()

        job_ids = {j.id for j in runner.scheduler.get_jobs()}
        assert _DIGEST_JOB_ID not in job_ids

        runner.scheduler.shutdown(wait=False)

    async def test_digest_content(self, tmp_path: Path):
        """The digest message includes today's run counts."""
        from scriptbox.runner import Runner

        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "ok.py").write_text(
            'META = {"name": "Ok"}\n\nasync def run(ctx):\n'
            '    return {"ok": True}\n'
        )

        db_path = str(tmp_path / "test.db")
        runner = Runner(
            str(scripts_dir), db_path, use_sandbox=False,
            daily_digest=True,
        )
        notifier = _make_notifier()
        runner.set_notifier(notifier)
        await runner.setup()
        notifier.send.reset_mock()

        # Create a run so the digest has data.
        await runner.trigger("ok")
        notifier.send.reset_mock()

        await runner._send_daily_digest()

        assert notifier.send.await_count == 1
        digest = notifier.send.call_args.args[0]
        assert "Daily Digest" in digest
        assert "Runs today:" in digest
        assert "Success:" in digest

        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Docker integration test (requires running Docker)
# ---------------------------------------------------------------------------


@pytest.mark.docker
class TestDockerNotificationIntegration:
    async def test_sandboxed_script_telegram_dispatched(self, tmp_path: Path):
        """A sandboxed script calling ctx.telegram.send() produces a
        notifications.json that the DockerExecutor dispatches."""
        from scriptbox.sandbox.harness import run_harness

        run_dir = tmp_path / "run"
        run_dir.mkdir()

        script_src = """\
META = {"name": "Notifier test"}

async def run(ctx):
    await ctx.telegram.send("hello from container")
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
        assert messages[0]["text"] == "hello from container"

        # Now simulate DockerExecutor dispatching.
        notifier = _make_notifier()
        from scriptbox.sandbox.docker_executor import DockerExecutor

        de = object.__new__(DockerExecutor)
        de._notifier = notifier
        await de._dispatch_notifications(run_dir)

        notifier.send.assert_awaited_once_with(
            "hello from container", parse_mode="HTML"
        )
