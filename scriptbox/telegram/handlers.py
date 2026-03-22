"""PTB command handlers for the ScriptBox Telegram bot.

Every handler receives ``(update, context)`` and accesses a
:class:`~scriptbox.runner.Runner` via ``context.bot_data["runner"]``.

All handlers are async, catch exceptions, and reply with user-friendly
messages.  Wrap them with :func:`require_auth` before registering.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from scriptbox.telegram.formatter import (
    format_dag,
    format_execution_results,
    format_logs,
    format_script_list,
    format_stats,
    format_status,
)

logger = logging.getLogger(__name__)

_PAUSED_FILE = Path("paused.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_runner(context: Any):
    return context.bot_data["runner"]


def _load_paused() -> set[str]:
    if _PAUSED_FILE.exists():
        try:
            return set(json.loads(_PAUSED_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _save_paused(paused: set[str]) -> None:
    _PAUSED_FILE.write_text(json.dumps(sorted(paused)))


async def _reply(update: Any, text: str) -> None:
    await update.effective_message.reply_text(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /scripts
# ---------------------------------------------------------------------------


async def cmd_scripts(update: Any, context: Any) -> None:
    """/scripts — list all loaded scripts with status."""
    try:
        runner = _get_runner(context)
        scripts = runner.get_scripts()
        paused = _load_paused()
        await _reply(update, format_script_list(scripts, paused=paused))
    except Exception as exc:
        logger.error("cmd_scripts failed", exc_info=True)
        await _reply(update, f"❌ Error listing scripts: {exc}")


# ---------------------------------------------------------------------------
# /run <script_id>
# ---------------------------------------------------------------------------


async def cmd_run(update: Any, context: Any) -> None:
    """/run <script_id> — trigger a script manually."""
    try:
        runner = _get_runner(context)
        args = context.args or []
        if not args:
            await _reply(update, "Usage: /run &lt;script_id&gt;")
            return

        script_id = args[0]
        script_ids = {s.id for s in runner.get_scripts()}
        if script_id not in script_ids:
            await _reply(update, f"❌ Script '{script_id}' not found")
            return

        await _reply(update, f"⏳ Running <code>{script_id}</code>...")
        results = await runner.trigger(script_id)
        await _reply(update, format_execution_results(results))
    except Exception as exc:
        logger.error("cmd_run failed", exc_info=True)
        await _reply(update, f"❌ Error running script: {exc}")


# ---------------------------------------------------------------------------
# /logs <script_id> [n]
# ---------------------------------------------------------------------------


async def cmd_logs(update: Any, context: Any) -> None:
    """/logs <script_id> [n] — show recent runs."""
    try:
        runner = _get_runner(context)
        args = context.args or []
        if not args:
            await _reply(update, "Usage: /logs &lt;script_id&gt; [n]")
            return

        script_id = args[0]
        limit = 5
        if len(args) >= 2:
            try:
                limit = int(args[1])
            except ValueError:
                pass

        runs = await runner.run_logger.get_runs(script_id=script_id, limit=limit)
        await _reply(update, format_logs(runs, script_id))
    except Exception as exc:
        logger.error("cmd_logs failed", exc_info=True)
        await _reply(update, f"❌ Error fetching logs: {exc}")


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


async def cmd_status(update: Any, context: Any) -> None:
    """/status — system overview."""
    try:
        runner = _get_runner(context)
        scripts = runner.get_scripts()

        runs = await runner.run_logger.get_runs(limit=1)
        last_run = runs[0]["started_at"] if runs else None

        docker_available = False
        try:
            import docker

            docker.from_env().ping()
            docker_available = True
        except Exception:
            pass

        runner_info = {
            "last_run": last_run,
            "docker": docker_available,
        }
        await _reply(update, format_status(scripts, runner_info))
    except Exception as exc:
        logger.error("cmd_status failed", exc_info=True)
        await _reply(update, f"❌ Error fetching status: {exc}")


# ---------------------------------------------------------------------------
# /pause <script_id>
# ---------------------------------------------------------------------------


async def cmd_pause(update: Any, context: Any) -> None:
    """/pause <script_id> — pause a script's cron schedule."""
    try:
        runner = _get_runner(context)
        args = context.args or []
        if not args:
            await _reply(update, "Usage: /pause &lt;script_id&gt;")
            return

        script_id = args[0]
        script_ids = {s.id for s in runner.get_scripts()}
        if script_id not in script_ids:
            await _reply(update, f"❌ Script '{script_id}' not found")
            return

        paused = _load_paused()
        if script_id in paused:
            await _reply(update, f"⏸ <code>{script_id}</code> is already paused.")
            return

        # Remove the APScheduler job if it exists.
        try:
            runner.scheduler.remove_job(script_id)
        except Exception:
            pass  # No cron job for this script — still mark as paused.

        paused.add(script_id)
        _save_paused(paused)
        await _reply(update, f"⏸ Paused <code>{script_id}</code>. Use /resume to re-enable.")
    except Exception as exc:
        logger.error("cmd_pause failed", exc_info=True)
        await _reply(update, f"❌ Error pausing script: {exc}")


# ---------------------------------------------------------------------------
# /resume <script_id>
# ---------------------------------------------------------------------------


async def cmd_resume(update: Any, context: Any) -> None:
    """/resume <script_id> — resume a paused script."""
    try:
        runner = _get_runner(context)
        args = context.args or []
        if not args:
            await _reply(update, "Usage: /resume &lt;script_id&gt;")
            return

        script_id = args[0]
        paused = _load_paused()
        if script_id not in paused:
            await _reply(update, f"▶️ <code>{script_id}</code> is not paused.")
            return

        # Re-register the APScheduler job.
        script = next((s for s in runner.get_scripts() if s.id == script_id), None)
        if script and script.meta.get("schedule"):
            from apscheduler.triggers.cron import CronTrigger

            parts = script.meta["schedule"].strip().split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                )
                runner.scheduler.add_job(
                    runner._cron_callback,
                    trigger=trigger,
                    args=[script_id],
                    id=script_id,
                    replace_existing=True,
                )

        paused.discard(script_id)
        _save_paused(paused)
        await _reply(update, f"▶️ Resumed <code>{script_id}</code>.")
    except Exception as exc:
        logger.error("cmd_resume failed", exc_info=True)
        await _reply(update, f"❌ Error resuming script: {exc}")


# ---------------------------------------------------------------------------
# /stats [script_id]
# ---------------------------------------------------------------------------


async def cmd_stats(update: Any, context: Any) -> None:
    """/stats [script_id] — show stats."""
    try:
        runner = _get_runner(context)
        args = context.args or []
        script_id = args[0] if args else None

        stats = await runner.run_logger.get_stats(script_id=script_id)
        await _reply(update, format_stats(stats, script_id=script_id))
    except Exception as exc:
        logger.error("cmd_stats failed", exc_info=True)
        await _reply(update, f"❌ Error fetching stats: {exc}")


# ---------------------------------------------------------------------------
# /graph <script_id>
# ---------------------------------------------------------------------------


async def cmd_graph(update: Any, context: Any) -> None:
    """/graph [script_id] — show DAG for a script."""
    try:
        runner = _get_runner(context)
        args = context.args or []
        target = args[0] if args else None

        dag = runner.get_dag()
        await _reply(update, format_dag(dag, target=target))
    except Exception as exc:
        logger.error("cmd_graph failed", exc_info=True)
        await _reply(update, f"❌ Error fetching graph: {exc}")


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
🤖 <b>ScriptBox Commands</b>

/scripts — List all loaded scripts
/run &lt;script_id&gt; — Trigger a script
/logs &lt;script_id&gt; [n] — Show recent runs (default 5)
/status — System overview
/stats [script_id] — Show stats (per-script or aggregate)
/graph [script_id] — Show dependency graph
/pause &lt;script_id&gt; — Pause a script's schedule
/resume &lt;script_id&gt; — Resume a paused script
/help — Show this message"""


async def cmd_help(update: Any, context: Any) -> None:
    """/help — list all commands."""
    await _reply(update, _HELP_TEXT)
