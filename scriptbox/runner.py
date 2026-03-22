from __future__ import annotations

import asyncio
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scriptbox.dag import build_graph
from scriptbox.executor import ExecutionResult, execute
from scriptbox.loader import ScriptInfo, load_scripts
from scriptbox.observability import RunLogger

logger = logging.getLogger(__name__)


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
        secrets_path: str | None = None,
    ) -> None:
        self._scripts_dir = scripts_dir
        self._db_path = db_path
        self._secrets_path = secrets_path
        self._scripts: list[ScriptInfo] = []
        self._run_logger = RunLogger(db_path)
        self._scheduler = AsyncIOScheduler()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Load scripts, build the DAG, and register scheduled jobs."""
        self._scripts = load_scripts(self._scripts_dir)
        self._sync_scheduler_jobs()
        if not self._scheduler.running:
            self._scheduler.start()
        logger.info(
            "Runner ready – %d script(s) loaded, %d scheduled",
            len(self._scripts),
            len(self._scheduler.get_jobs()),
        )

    async def trigger(self, script_id: str) -> list[ExecutionResult]:
        """Manually run *script_id* (with full DAG resolution) and log results."""
        results = await execute(
            self._scripts, script_id, self._db_path, self._secrets_path
        )
        for r in results:
            trigger_type = "dependency" if r.script_id != script_id else "manual"
            await self._run_logger.log_run(r, trigger=trigger_type)
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
            if job_id not in desired:
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
        """Called by APScheduler when a cron job fires."""
        logger.info("Cron triggered: %s", script_id)
        results = await execute(
            self._scripts, script_id, self._db_path, self._secrets_path
        )
        for r in results:
            trigger_type = "cron" if r.script_id == script_id else "dependency"
            await self._run_logger.log_run(r, trigger=trigger_type)
