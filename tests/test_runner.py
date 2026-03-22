from __future__ import annotations

from pathlib import Path

import pytest

from scriptbox.runner import Runner

# ---------------------------------------------------------------------------
# Helpers — write script files into a temp directory
# ---------------------------------------------------------------------------

_STANDALONE = """\
META = {"name": "Standalone"}

async def run(ctx):
    return {"value": 42}
"""

_UPSTREAM = """\
META = {"name": "Upstream"}

async def run(ctx):
    return {"data": "from_upstream"}
"""

_DOWNSTREAM = """\
META = {"name": "Downstream", "depends_on": ["upstream"]}

async def run(ctx):
    received = ctx.inputs["upstream"]["data"]
    return {"received": received}
"""

_SCHEDULED = """\
META = {"name": "Scheduled", "schedule": "*/10 * * * *"}

async def run(ctx):
    return {"ran": True}
"""

_NO_SCHEDULE = """\
META = {"name": "No Schedule"}

async def run(ctx):
    return {"ok": True}
"""


def _write(scripts_dir: Path, name: str, content: str) -> Path:
    p = scripts_dir / f"{name}.py"
    p.write_text(content)
    return p


@pytest.fixture()
def scripts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "runner_test.db")


# ---------------------------------------------------------------------------
# Setup — scripts are loaded
# ---------------------------------------------------------------------------


class TestSetup:
    @pytest.mark.asyncio
    async def test_scripts_loaded(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "standalone", _STANDALONE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        assert len(runner.get_scripts()) == 1
        assert runner.get_scripts()[0].id == "standalone"
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_dag_populated(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "upstream", _UPSTREAM)
        _write(scripts_dir, "downstream", _DOWNSTREAM)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        dag = runner.get_dag()
        assert dag == {"downstream": ["upstream"], "upstream": []}
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# trigger() — standalone
# ---------------------------------------------------------------------------


class TestTriggerStandalone:
    @pytest.mark.asyncio
    async def test_returns_success(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "standalone", _STANDALONE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        results = await runner.trigger("standalone")
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].outputs == {"value": 42}
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_logged_to_observability(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "standalone", _STANDALONE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        await runner.trigger("standalone")
        runs = await runner.run_logger.get_runs(script_id="standalone")
        assert len(runs) == 1
        assert runs[0]["status"] == "success"
        assert runs[0]["trigger"] == "manual"
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# trigger() — chain with dependencies
# ---------------------------------------------------------------------------


class TestTriggerChain:
    @pytest.mark.asyncio
    async def test_chain_runs_in_order(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "upstream", _UPSTREAM)
        _write(scripts_dir, "downstream", _DOWNSTREAM)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        results = await runner.trigger("downstream")
        ids = [r.script_id for r in results]
        assert ids == ["upstream", "downstream"]
        assert all(r.status == "success" for r in results)
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_downstream_receives_input(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "upstream", _UPSTREAM)
        _write(scripts_dir, "downstream", _DOWNSTREAM)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        results = await runner.trigger("downstream")
        downstream = next(r for r in results if r.script_id == "downstream")
        assert downstream.outputs == {"received": "from_upstream"}
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_chain_logged_with_triggers(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "upstream", _UPSTREAM)
        _write(scripts_dir, "downstream", _DOWNSTREAM)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        await runner.trigger("downstream")
        runs = await runner.run_logger.get_runs()
        triggers = {r["script_id"]: r["trigger"] for r in runs}
        assert triggers["downstream"] == "manual"
        assert triggers["upstream"] == "dependency"
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# get_runs after trigger
# ---------------------------------------------------------------------------


class TestGetRunsAfterTrigger:
    @pytest.mark.asyncio
    async def test_runs_persisted(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "standalone", _STANDALONE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        await runner.trigger("standalone")
        await runner.trigger("standalone")
        runs = await runner.run_logger.get_runs()
        assert len(runs) == 2
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# reload() — picks up new file
# ---------------------------------------------------------------------------


class TestReloadNewScript:
    @pytest.mark.asyncio
    async def test_new_script_discovered(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "standalone", _STANDALONE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        assert len(runner.get_scripts()) == 1

        _write(scripts_dir, "another", _NO_SCHEDULE)
        await runner.reload()
        assert len(runner.get_scripts()) == 2
        ids = {s.id for s in runner.get_scripts()}
        assert "another" in ids
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# reload() — removes job for deleted script
# ---------------------------------------------------------------------------


class TestReloadRemovesJob:
    @pytest.mark.asyncio
    async def test_deleted_script_job_removed(self, scripts_dir: Path, db_path: str):
        sched_path = _write(scripts_dir, "scheduled", _SCHEDULED)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        assert len(runner.scheduler.get_jobs()) == 1
        assert runner.scheduler.get_jobs()[0].id == "scheduled"

        sched_path.unlink()
        await runner.reload()
        assert len(runner.scheduler.get_jobs()) == 0
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Scheduling — only scripts with "schedule" get jobs
# ---------------------------------------------------------------------------


class TestScheduling:
    @pytest.mark.asyncio
    async def test_no_schedule_means_no_job(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "no_sched", _NO_SCHEDULE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        assert len(runner.scheduler.get_jobs()) == 0
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_no_schedule_still_triggerable(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "no_sched", _NO_SCHEDULE)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        results = await runner.trigger("no_sched")
        assert len(results) == 1
        assert results[0].status == "success"
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_scheduled_script_gets_job(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "scheduled", _SCHEDULED)
        runner = Runner(str(scripts_dir), db_path, use_sandbox=False)
        await runner.setup()
        jobs = runner.scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "scheduled"
        runner.scheduler.shutdown(wait=False)
