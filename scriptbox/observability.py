from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from scriptbox.executor import ExecutionResult

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    outputs_json TEXT,
    trigger TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id),
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER
);
"""


class RunLogger:
    """Logs script executions to SQLite for observability."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._table_ready = False

    async def _ensure_tables(self, db: aiosqlite.Connection) -> None:
        if not self._table_ready:
            await db.executescript(_CREATE_TABLES)
            await db.commit()
            self._table_ready = True

    async def log_run(
        self, result: ExecutionResult, trigger: str = "manual"
    ) -> int:
        """Persist an execution result and return the new run id."""
        started_at = datetime.now(timezone.utc).isoformat()
        outputs_json = json.dumps(result.outputs) if result.outputs is not None else None

        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_tables(db)
            cursor = await db.execute(
                "INSERT INTO runs (script_id, started_at, duration_ms, status, error, outputs_json, trigger) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result.script_id,
                    started_at,
                    result.duration_ms,
                    result.status,
                    result.error,
                    outputs_json,
                    trigger,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_runs(
        self, script_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return recent runs, newest first."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await self._ensure_tables(db)

            if script_id is not None:
                cursor = await db.execute(
                    "SELECT * FROM runs WHERE script_id = ? ORDER BY id DESC LIMIT ?",
                    (script_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM runs ORDER BY id DESC LIMIT ?",
                    (limit,),
                )

            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_stats(self, script_id: str | None = None) -> dict[str, Any]:
        """Aggregate statistics, optionally filtered to one script."""
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_tables(db)

            if script_id is not None:
                cursor = await db.execute(
                    "SELECT "
                    "  COUNT(*) AS total_runs, "
                    "  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count, "
                    "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failure_count, "
                    "  AVG(duration_ms) AS avg_duration_ms "
                    "FROM runs WHERE script_id = ?",
                    (script_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT "
                    "  COUNT(*) AS total_runs, "
                    "  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count, "
                    "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failure_count, "
                    "  AVG(duration_ms) AS avg_duration_ms "
                    "FROM runs",
                )

            row = await cursor.fetchone()
            total = row[0] or 0
            success = row[1] or 0
            failure = row[2] or 0
            avg_dur = row[3] or 0.0

            # Sum cost from llm_calls joined to the relevant runs.
            if script_id is not None:
                cost_cursor = await db.execute(
                    "SELECT COALESCE(SUM(lc.cost_usd), 0.0) "
                    "FROM llm_calls lc JOIN runs r ON lc.run_id = r.id "
                    "WHERE r.script_id = ?",
                    (script_id,),
                )
            else:
                cost_cursor = await db.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls",
                )

            cost_row = await cost_cursor.fetchone()
            total_cost = cost_row[0] if cost_row else 0.0

            return {
                "total_runs": total,
                "success_count": success,
                "failure_count": failure,
                "success_rate": success / total if total > 0 else 0.0,
                "avg_duration_ms": round(avg_dur, 2),
                "total_cost_usd": total_cost,
            }

    async def get_all_script_stats(self) -> list[dict[str, Any]]:
        """Return per-script stats for every script that has been logged."""
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_tables(db)

            cursor = await db.execute(
                "SELECT DISTINCT script_id FROM runs ORDER BY script_id"
            )
            rows = await cursor.fetchall()
            script_ids = [r[0] for r in rows]

        return [
            {"script_id": sid, **(await self.get_stats(sid))}
            for sid in script_ids
        ]
