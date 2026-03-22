from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scriptbox.dag import build_graph
from scriptbox.executor import ExecutionResult, execute
from scriptbox.loader import ScriptInfo, load_scripts
from scriptbox.observability import RunLogger

logger = logging.getLogger(__name__)

_DIGEST_JOB_ID = "__scriptbox_daily_digest__"


def _parse_cron(expr: str) -> dict[str, str]:
    """Split a 5-field cron string into CronTrigger kwargs."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got: {expr!r}")
    return dict(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )


class Runner:
    """Main entry point that ties loader, DAG, executor, scheduler, and observability together."""

    def __init__(
        self,
        scripts_dir: str,
        db_path: str = "./scriptbox.db",
        use_sandbox: bool = True,
        daily_digest: bool = False,
        daily_digest_time: str = "09:00",
    ) -> None:
        self._scripts_dir = scripts_dir
        self._db_path = db_path
        self._use_sandbox = use_sandbox
        self._scripts: list[ScriptInfo] = []
        self._run_logger = RunLogger(db_path)
        self._scheduler = AsyncIOScheduler()
        self._notifier: Any | None = None
        self._daily_digest = daily_digest
        self._daily_digest_time = daily_digest_time

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_notifier(self, notifier: Any) -> None:
        """Attach a :class:`~scriptbox.telegram.notifier.TelegramNotifier`.

        When set, cron-triggered script *failures* produce an immediate
        Telegram alert.  Successful scripts are silent unless the script
        itself calls ``ctx.telegram.send()`` — those messages are
        forwarded by the executor (sandbox or local).
        """
        self._notifier = notifier

    async def setup(self) -> None:
        """Load scripts, build the DAG, and register scheduled jobs."""
        self._scripts = load_scripts(self._scripts_dir)
        self._sync_scheduler_jobs()
        if self._daily_digest and self._notifier:
            self._schedule_daily_digest()
        if not self._scheduler.running:
            self._scheduler.start()
        total = len(self._scripts)
        scheduled = len(self._scheduler.get_jobs())
        # Don't count the digest job itself.
        if self._daily_digest:
            scheduled = max(0, scheduled - 1)
        logger.info(
            "Runner ready – %d script(s) loaded, %d scheduled, sandbox=%s",
            total,
            scheduled,
            self._use_sandbox,
        )
        if self._notifier:
            await self._notifier.send(
                f"🟢 ScriptBox started. {total} scripts loaded, {scheduled} scheduled."
            )

    async def trigger(self, script_id: str) -> list[ExecutionResult]:
        """Manually run *script_id* (with full DAG resolution) and log results."""
        results = await execute(
            self._scripts,
            script_id,
            self._db_path,
            use_sandbox=self._use_sandbox,
            notifier=self._notifier,
        )
        for r in results:
            trigger_type = "dependency" if r.script_id != script_id else "manual"
            await self._run_logger.log_run(r, trigger=trigger_type)
            logger.info(
                "Script %s: status=%s sandboxed=%s",
                r.script_id,
                r.status,
                r.sandboxed,
            )
        return results

    async def reload(self) -> None:
        """Re-scan the scripts directory and update scheduler jobs."""
        self._scripts = load_scripts(self._scripts_dir)
        self._sync_scheduler_jobs()
        logger.info(
            "Reloaded – %d script(s), %d scheduled",
            len(self._scripts),
            len(self._scheduler.get_jobs()),
        )

    def start(self) -> None:
        """Blocking call that runs the asyncio event loop with the scheduler."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.setup())
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            loop.close()

    async def cleanup(self) -> dict:
        """Clean up sandbox containers, image, and orphaned store directories."""
        from scriptbox.sandbox.image_builder import ImageBuilder

        info: dict[str, Any] = {}
        try:
            builder = ImageBuilder()
            info["containers_removed"] = builder.cleanup_containers()
            info["image_removed"] = builder.cleanup_image()
        except Exception as exc:
            logger.warning("Sandbox cleanup failed: %s", exc)
            info["containers_removed"] = 0
            info["image_removed"] = False

        # Remove orphaned store directories (those without a matching script).
        stores_dir = Path(self._db_path).parent / "stores"
        orphans_removed = 0
        if stores_dir.is_dir():
            script_ids = {s.id for s in self._scripts}
            for child in stores_dir.iterdir():
                if child.is_dir() and child.name not in script_ids:
                    import shutil

                    shutil.rmtree(child, ignore_errors=True)
                    orphans_removed += 1
                    logger.info("Removed orphaned store dir: %s", child.name)
        info["orphaned_stores_removed"] = orphans_removed
        return info

    def get_scripts(self) -> list[ScriptInfo]:
        return list(self._scripts)

    def get_dag(self) -> dict[str, list[str]]:
        return build_graph(self._scripts)

    @property
    def run_logger(self) -> RunLogger:
        return self._run_logger

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sync_scheduler_jobs(self) -> None:
        """Reconcile scheduler jobs with the current set of loaded scripts."""
        desired: dict[str, str] = {}
        for s in self._scripts:
            cron_expr = s.meta.get("schedule")
            if cron_expr:
                desired[s.id] = cron_expr

        existing_ids = {job.id for job in self._scheduler.get_jobs()}

        # Remove jobs for scripts that are gone or no longer scheduled.
        for job_id in existing_ids:
            if job_id not in desired and job_id != _DIGEST_JOB_ID:
                self._scheduler.remove_job(job_id)

        # Add / update jobs.
        for script_id, cron_expr in desired.items():
            trigger = CronTrigger(**_parse_cron(cron_expr))
            if script_id in existing_ids:
                self._scheduler.reschedule_job(script_id, trigger=trigger)
            else:
                self._scheduler.add_job(
                    self._cron_callback,
                    trigger=trigger,
                    args=[script_id],
                    id=script_id,
                    replace_existing=True,
                )

    async def _cron_callback(self, script_id: str) -> None:
        """Called by APScheduler when a cron job fires.

        Notification policy:
        1. Before execution: nothing (avoid spam for routine runs).
        2. On success: silent — script's own ``ctx.telegram.send()`` calls
           are forwarded by the executor; the runner adds nothing extra.
        3. On failure: always send a detailed alert.
        4. On DAG partial failure: send a summary of what ran / skipped.
        """
        logger.info("Cron triggered: %s", script_id)
        results = await execute(
            self._scripts,
            script_id,
            self._db_path,
            use_sandbox=self._use_sandbox,
            notifier=self._notifier,
        )

        # Log every result and capture run IDs for failure alerts.
        run_ids: dict[str, int] = {}
        for r in results:
            trigger_type = "cron" if r.script_id == script_id else "dependency"
            run_id = await self._run_logger.log_run(r, trigger=trigger_type)
            run_ids[r.script_id] = run_id

        if not self._notifier:
            return

        failed = [r for r in results if r.status == "failed"]
        skipped = [r for r in results if r.status == "skipped"]
        succeeded = [r for r in results if r.status == "success"]

        # --- Per-failure alerts -------------------------------------------
        for r in failed:
            run_id = run_ids.get(r.script_id, 0)
            await self._notifier.send(
                f"🔴 <b>Script failed: {r.script_id}</b>\n"
                f"Error: {r.error or 'unknown error'}\n"
                f"Duration: {r.duration_ms:,}ms\n"
                f"Run ID: #{run_id}"
            )

        # --- DAG partial failure summary ----------------------------------
        if len(results) > 1 and failed and (succeeded or skipped):
            lines = [f"⚠️ <b>DAG partial failure for {script_id}</b>"]
            for r in succeeded:
                lines.append(f"  ✅ {r.script_id} — {r.duration_ms:,}ms")
            for r in failed:
                lines.append(f"  ❌ {r.script_id} — {r.error or 'error'}")
            for r in skipped:
                lines.append(f"  ⏭ {r.script_id} — skipped")
            await self._notifier.send("\n".join(lines))

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------

    def _schedule_daily_digest(self) -> None:
        """Register an APScheduler job for the daily digest."""
        parts = self._daily_digest_time.split(":")
        hour = parts[0] if parts else "9"
        minute = parts[1] if len(parts) > 1 else "0"
        self._scheduler.add_job(
            self._send_daily_digest,
            trigger=CronTrigger(hour=hour, minute=minute),
            id=_DIGEST_JOB_ID,
            replace_existing=True,
        )

    async def _send_daily_digest(self) -> None:
        """Build and send a daily summary of today's runs."""
        if not self._notifier:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        runs = await self._run_logger.get_runs_since(today)

        total = len(runs)
        success = sum(1 for r in runs if r.get("status") == "success")
        failures = [r for r in runs if r.get("status") == "failed"]
        rate = (success / total * 100) if total > 0 else 0

        lines = ["📊 <b>Daily Digest</b>", f"Runs today: {total}"]
        lines.append(f"Success: {success} ({rate:.1f}%)")

        if failures:
            parts = []
            for f in failures:
                started = f.get("started_at", "")
                time_str = started[11:16] if len(started) > 16 else "?"
                sid = f.get("script_id", "?")
                err = f.get("error") or "error"
                if len(err) > 40:
                    err = err[:40] + "…"
                parts.append(f"{sid} at {time_str} — {err}")
            lines.append(f"Failures: {len(failures)} ({'; '.join(parts)})")
        else:
            lines.append("Failures: 0")

        # LLM cost (from llm_calls table — currently unpopulated but ready).
        stats = await self._run_logger.get_stats()
        cost = stats.get("total_cost_usd", 0.0)
        if cost > 0:
            lines.append(f"LLM cost today: ${cost:.2f}")

        await self._notifier.send("\n".join(lines))
