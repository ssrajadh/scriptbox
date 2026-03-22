from __future__ import annotations

from pathlib import Path

import pytest

from scriptbox.executor import ExecutionResult
from scriptbox.observability import RunLogger


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "obs_test.db")


def _success_result(script_id: str = "my_script", duration_ms: int = 100) -> ExecutionResult:
    return ExecutionResult(
        script_id=script_id,
        status="success",
        duration_ms=duration_ms,
        error=None,
        outputs={"data": "ok"},
    )


def _failed_result(script_id: str = "my_script", duration_ms: int = 50) -> ExecutionResult:
    return ExecutionResult(
        script_id=script_id,
        status="failed",
        duration_ms=duration_ms,
        error="something broke",
        outputs=None,
    )


# ---------------------------------------------------------------------------
# Log and retrieve a successful run
# ---------------------------------------------------------------------------


class TestLogSuccessfulRun:
    @pytest.mark.asyncio
    async def test_log_returns_run_id(self, db_path: str):
        logger = RunLogger(db_path)
        run_id = await logger.log_run(_success_result())
        assert isinstance(run_id, int)
        assert run_id >= 1

    @pytest.mark.asyncio
    async def test_get_runs_returns_logged_run(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result())
        runs = await logger.get_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run["script_id"] == "my_script"
        assert run["status"] == "success"
        assert run["duration_ms"] == 100
        assert run["error"] is None
        assert '"data"' in run["outputs_json"]
        assert run["trigger"] == "manual"

    @pytest.mark.asyncio
    async def test_started_at_is_iso(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result())
        runs = await logger.get_runs()
        # ISO timestamps contain "T" and end with timezone info
        assert "T" in runs[0]["started_at"]


# ---------------------------------------------------------------------------
# Log and retrieve a failed run
# ---------------------------------------------------------------------------


class TestLogFailedRun:
    @pytest.mark.asyncio
    async def test_error_stored(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_failed_result())
        runs = await logger.get_runs()
        assert runs[0]["error"] == "something broke"

    @pytest.mark.asyncio
    async def test_outputs_null_for_failure(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_failed_result())
        runs = await logger.get_runs()
        assert runs[0]["outputs_json"] is None


# ---------------------------------------------------------------------------
# Ordering: newest first
# ---------------------------------------------------------------------------


class TestOrdering:
    @pytest.mark.asyncio
    async def test_newest_first(self, db_path: str):
        logger = RunLogger(db_path)
        id1 = await logger.log_run(_success_result())
        id2 = await logger.log_run(_success_result())
        runs = await logger.get_runs()
        assert runs[0]["id"] == id2
        assert runs[1]["id"] == id1


# ---------------------------------------------------------------------------
# Filtering by script_id
# ---------------------------------------------------------------------------


class TestFiltering:
    @pytest.mark.asyncio
    async def test_filter_by_script_id(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result("alpha"))
        await logger.log_run(_success_result("beta"))
        await logger.log_run(_success_result("alpha"))

        alpha_runs = await logger.get_runs(script_id="alpha")
        assert len(alpha_runs) == 2
        assert all(r["script_id"] == "alpha" for r in alpha_runs)

        beta_runs = await logger.get_runs(script_id="beta")
        assert len(beta_runs) == 1


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_success_rate(self, db_path: str):
        logger = RunLogger(db_path)
        for _ in range(3):
            await logger.log_run(_success_result("s1", duration_ms=100))
        await logger.log_run(_failed_result("s1", duration_ms=200))

        stats = await logger.get_stats("s1")
        assert stats["total_runs"] == 4
        assert stats["success_count"] == 3
        assert stats["failure_count"] == 1
        assert stats["success_rate"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_avg_duration(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result("s1", duration_ms=100))
        await logger.log_run(_success_result("s1", duration_ms=200))

        stats = await logger.get_stats("s1")
        assert stats["avg_duration_ms"] == pytest.approx(150.0)

    @pytest.mark.asyncio
    async def test_no_runs_returns_zeros(self, db_path: str):
        logger = RunLogger(db_path)
        stats = await logger.get_stats("nonexistent")
        assert stats["total_runs"] == 0
        assert stats["success_count"] == 0
        assert stats["failure_count"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["avg_duration_ms"] == 0.0
        assert stats["total_cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_total_cost_defaults_to_zero(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result())
        stats = await logger.get_stats()
        assert stats["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# get_all_script_stats
# ---------------------------------------------------------------------------


class TestGetAllScriptStats:
    @pytest.mark.asyncio
    async def test_separate_entries(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result("alpha"))
        await logger.log_run(_success_result("beta"))
        await logger.log_run(_failed_result("beta"))

        all_stats = await logger.get_all_script_stats()
        assert len(all_stats) == 2

        by_id = {s["script_id"]: s for s in all_stats}
        assert by_id["alpha"]["total_runs"] == 1
        assert by_id["alpha"]["success_rate"] == 1.0
        assert by_id["beta"]["total_runs"] == 2
        assert by_id["beta"]["success_rate"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_empty_when_no_runs(self, db_path: str):
        logger = RunLogger(db_path)
        assert await logger.get_all_script_stats() == []


# ---------------------------------------------------------------------------
# Trigger field
# ---------------------------------------------------------------------------


class TestTriggerField:
    @pytest.mark.asyncio
    async def test_custom_trigger(self, db_path: str):
        logger = RunLogger(db_path)
        await logger.log_run(_success_result(), trigger="cron")
        runs = await logger.get_runs()
        assert runs[0]["trigger"] == "cron"
