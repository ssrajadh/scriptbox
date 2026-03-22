"""End-to-end integration tests for the full Phase 1 pipeline."""
from __future__ import annotations

from pathlib import Path

import pytest

from scriptbox.runner import Runner
from scriptbox.store import ScriptStore

# ---------------------------------------------------------------------------
# Script sources
# ---------------------------------------------------------------------------

FETCH_DATA = """\
META = {"name": "Fetch Data", "schedule": "0 * * * *", "outputs": ["items"]}

async def run(ctx):
    return {"items": [{"id": 1, "title": "Hello"}, {"id": 2, "title": "World"}]}
"""

FETCH_DATA_BROKEN = """\
META = {"name": "Fetch Data", "schedule": "0 * * * *", "outputs": ["items"]}

async def run(ctx):
    raise RuntimeError("data source unavailable")
"""

FILTER_DATA = """\
META = {"name": "Filter Data", "depends_on": ["fetch_data"], "outputs": ["filtered"]}

async def run(ctx):
    items = ctx.inputs["fetch_data"]["items"]
    filtered = [i for i in items if i["id"] > 1]
    return {"filtered": filtered}
"""

NOTIFY = """\
META = {"name": "Notify", "depends_on": ["filter_data"]}

async def run(ctx):
    filtered = ctx.inputs["filter_data"]["filtered"]
    await ctx.store.set("last_count", len(filtered))
"""

STANDALONE = """\
META = {"name": "Standalone", "schedule": "0 8 * * *"}

async def run(ctx):
    await ctx.store.set("status", "executed")
"""

EXTRA_SCRIPT = """\
META = {"name": "Extra"}

async def run(ctx):
    return {"extra": True}
"""

READER_SCRIPT = """\
from scriptbox.store import ScriptStore

META = {"name": "Reader"}

async def run(ctx):
    # Read from standalone's namespace using the same db
    store = ScriptStore(ctx.store._db_path, "standalone")
    val = await store.get("status")
    return {"read_status": val}
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(d: Path, name: str, src: str) -> Path:
    p = d / f"{name}.py"
    p.write_text(src)
    return p


@pytest.fixture()
def scripts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "integration.db")


async def _make_runner(scripts_dir: Path, db_path: str) -> Runner:
    runner = Runner(str(scripts_dir), db_path)
    await runner.setup()
    return runner


# ---------------------------------------------------------------------------
# 1. Full DAG chain
# ---------------------------------------------------------------------------


class TestFullDAGChain:
    @pytest.mark.asyncio
    async def test_three_scripts_run_in_order(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("notify")

        assert [r.script_id for r in results] == [
            "fetch_data",
            "filter_data",
            "notify",
        ]
        assert all(r.status == "success" for r in results)
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_observability_logs_three_runs(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        await runner.trigger("notify")
        runs = await runner.run_logger.get_runs()

        assert len(runs) == 3
        statuses = {r["script_id"]: r["status"] for r in runs}
        assert all(s == "success" for s in statuses.values())
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_notify_store_has_last_count(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        await runner.trigger("notify")

        # Only item with id > 1 passes the filter → count = 1
        store = ScriptStore(db_path, "notify")
        assert await store.get("last_count") == 1
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_filter_output_correct(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("notify")
        filter_result = next(r for r in results if r.script_id == "filter_data")
        assert filter_result.outputs == {"filtered": [{"id": 2, "title": "World"}]}
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 2. Failure propagation
# ---------------------------------------------------------------------------


class TestFailurePropagation:
    @pytest.mark.asyncio
    async def test_upstream_failure_skips_downstream(
        self, scripts_dir: Path, db_path: str
    ):
        _write(scripts_dir, "fetch_data", FETCH_DATA_BROKEN)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("notify")

        by_id = {r.script_id: r for r in results}
        assert by_id["fetch_data"].status == "failed"
        assert "data source unavailable" in by_id["fetch_data"].error
        assert by_id["filter_data"].status == "skipped"
        assert by_id["notify"].status == "skipped"
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_failure_logged_to_observability(
        self, scripts_dir: Path, db_path: str
    ):
        _write(scripts_dir, "fetch_data", FETCH_DATA_BROKEN)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        await runner.trigger("notify")
        runs = await runner.run_logger.get_runs()

        statuses = {r["script_id"]: r["status"] for r in runs}
        assert statuses["fetch_data"] == "failed"
        assert statuses["filter_data"] == "skipped"
        assert statuses["notify"] == "skipped"
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 3. Standalone execution
# ---------------------------------------------------------------------------


class TestStandaloneExecution:
    @pytest.mark.asyncio
    async def test_only_standalone_runs(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        _write(scripts_dir, "standalone", STANDALONE)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("standalone")

        assert len(results) == 1
        assert results[0].script_id == "standalone"
        assert results[0].status == "success"
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_store_has_status(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "standalone", STANDALONE)
        runner = await _make_runner(scripts_dir, db_path)

        await runner.trigger("standalone")

        store = ScriptStore(db_path, "standalone")
        assert await store.get("status") == "executed"
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 4. Stats after multiple runs
# ---------------------------------------------------------------------------


class TestStatsAfterMultipleRuns:
    @pytest.mark.asyncio
    async def test_three_successes(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "filter_data", FILTER_DATA)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        for _ in range(3):
            await runner.trigger("notify")

        stats = await runner.run_logger.get_stats("fetch_data")
        assert stats["total_runs"] == 3
        assert stats["success_rate"] == pytest.approx(1.0)
        assert stats["failure_count"] == 0
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 5. Store persistence across Runner instances
# ---------------------------------------------------------------------------


class TestStorePersistence:
    @pytest.mark.asyncio
    async def test_data_survives_new_runner(self, scripts_dir: Path, db_path: str):
        # First runner: standalone writes to store
        _write(scripts_dir, "standalone", STANDALONE)
        runner1 = await _make_runner(scripts_dir, db_path)
        await runner1.trigger("standalone")
        runner1.scheduler.shutdown(wait=False)

        # Second runner with a reader script
        _write(scripts_dir, "reader", READER_SCRIPT)
        runner2 = await _make_runner(scripts_dir, db_path)
        results = await runner2.trigger("reader")

        reader_result = next(r for r in results if r.script_id == "reader")
        assert reader_result.status == "success"
        assert reader_result.outputs == {"read_status": "executed"}
        runner2.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 6. Reload picks up new scripts
# ---------------------------------------------------------------------------


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_and_trigger_new_script(
        self, scripts_dir: Path, db_path: str
    ):
        _write(scripts_dir, "fetch_data", FETCH_DATA)
        _write(scripts_dir, "standalone", STANDALONE)
        runner = await _make_runner(scripts_dir, db_path)
        assert len(runner.get_scripts()) == 2

        # Add a new script at runtime
        _write(scripts_dir, "extra", EXTRA_SCRIPT)
        await runner.reload()
        assert len(runner.get_scripts()) == 3
        assert "extra" in {s.id for s in runner.get_scripts()}

        # Trigger the new script
        results = await runner.trigger("extra")
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].outputs == {"extra": True}
        runner.scheduler.shutdown(wait=False)
