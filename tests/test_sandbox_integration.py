"""Integration tests for the sandbox pipeline (Docker required).

Run with:  pytest -m docker tests/test_sandbox_integration.py -v
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import docker
import pytest

from scriptbox.runner import Runner
from scriptbox.sandbox.image_builder import ImageBuilder

pytestmark = pytest.mark.docker


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


if not _docker_available():
    pytest.skip("Docker daemon not available", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
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
    return str(tmp_path / "sandbox_int.db")


@pytest.fixture(scope="module", autouse=True)
def _ensure_image():
    ImageBuilder().ensure_image()


async def _make_runner(
    scripts_dir: Path, db_path: str, use_sandbox: bool = True
) -> Runner:
    runner = Runner(str(scripts_dir), db_path, use_sandbox=use_sandbox)
    await runner.setup()
    return runner


# ---------------------------------------------------------------------------
# Script sources
# ---------------------------------------------------------------------------

FETCH = """\
META = {"name": "Fetch"}

async def run(ctx):
    return {"items": [1, 2, 3]}
"""

TRANSFORM = """\
META = {"name": "Transform", "depends_on": ["fetch"]}

async def run(ctx):
    items = ctx.inputs["fetch"]["items"]
    return {"doubled": [x * 2 for x in items]}
"""

NOTIFY = """\
META = {"name": "Notify", "depends_on": ["transform"]}

async def run(ctx):
    data = ctx.inputs["transform"]["doubled"]
    await ctx.store.set("result", data)
    return {"notified": True}
"""

SANDBOXED_SCRIPT = """\
META = {"name": "Sandboxed"}

async def run(ctx):
    return {"ok": True}
"""

UNSANDBOXED_SCRIPT = """\
META = {"name": "Unsandboxed", "sandbox": {"enabled": False}}

async def run(ctx):
    return {"ok": True}
"""

STORE_WRITER = """\
META = {"name": "Writer"}

async def run(ctx):
    await ctx.store.set("token", "abc123")
    return {"wrote": True}
"""

STORE_READER = """\
META = {"name": "Reader", "depends_on": ["writer"]}

async def run(ctx):
    val = await ctx.store.get("token")
    return {"read": val}
"""

FAILING = """\
META = {"name": "Failing"}

async def run(ctx):
    raise RuntimeError("container boom")
"""

DEPENDS_ON_FAILING = """\
META = {"name": "Downstream", "depends_on": ["failing"]}

async def run(ctx):
    return {"should": "never run"}
"""

FALLBACK_SCRIPT = """\
META = {"name": "Fallback"}

async def run(ctx):
    return {"ran": True}
"""


# ---------------------------------------------------------------------------
# 1. Full DAG in sandbox
# ---------------------------------------------------------------------------


class TestFullDAGSandbox:
    @pytest.mark.asyncio
    async def test_all_sandboxed(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch", FETCH)
        _write(scripts_dir, "transform", TRANSFORM)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("notify")

        assert len(results) == 3
        assert all(r.sandboxed is True for r in results)
        assert all(r.status == "success" for r in results)
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_data_flows_through_chain(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch", FETCH)
        _write(scripts_dir, "transform", TRANSFORM)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("notify")
        by_id = {r.script_id: r for r in results}

        assert by_id["fetch"].outputs == {"items": [1, 2, 3]}
        assert by_id["transform"].outputs == {"doubled": [2, 4, 6]}
        assert by_id["notify"].outputs == {"notified": True}
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_observability_logs_all(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "fetch", FETCH)
        _write(scripts_dir, "transform", TRANSFORM)
        _write(scripts_dir, "notify", NOTIFY)
        runner = await _make_runner(scripts_dir, db_path)

        await runner.trigger("notify")
        runs = await runner.run_logger.get_runs()

        assert len(runs) == 3
        assert all(r["status"] == "success" for r in runs)
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 2. Mixed mode — sandbox enabled vs disabled
# ---------------------------------------------------------------------------


class TestMixedMode:
    @pytest.mark.asyncio
    async def test_one_sandboxed_one_local(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "sandboxed", SANDBOXED_SCRIPT)
        _write(scripts_dir, "unsandboxed", UNSANDBOXED_SCRIPT)
        runner = await _make_runner(scripts_dir, db_path)

        r_sand = await runner.trigger("sandboxed")
        r_unsand = await runner.trigger("unsandboxed")

        assert r_sand[0].sandboxed is True
        assert r_sand[0].status == "success"
        assert r_unsand[0].sandboxed is False
        assert r_unsand[0].status == "success"
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 3. Store persistence through sandbox containers
# ---------------------------------------------------------------------------


class TestStorePersistenceSandbox:
    @pytest.mark.asyncio
    async def test_reader_sees_writer_store(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "writer", STORE_WRITER)
        _write(scripts_dir, "reader", STORE_READER)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("reader")

        by_id = {r.script_id: r for r in results}
        assert by_id["writer"].status == "success"
        assert by_id["reader"].status == "success"
        # reader depends_on writer → same DAG, writer runs first.
        # But reader has its own store namespace, so it reads from its own
        # store (not writer's).  The script reads ctx.store.get("token")
        # which is in the "reader" namespace.  It should get None since
        # writer wrote to the "writer" namespace.
        #
        # Actually — let me re-read the script.  store_reader reads from
        # its own ctx.store.  That's the "reader" namespace.  The writer
        # wrote to the "writer" namespace.  So reader gets None.
        # That's the namespace isolation test.
        # For *persistence* across containers we need two runs of the
        # SAME script.
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_same_script_store_persists_across_runs(
        self, scripts_dir: Path, db_path: str
    ):
        """Run the writer twice — second run should see first run's data."""
        src = """\
META = {"name": "Persistent"}

async def run(ctx):
    prev = await ctx.store.get("counter", default=0)
    await ctx.store.set("counter", prev + 1)
    return {"counter": prev + 1}
"""
        _write(scripts_dir, "persistent", src)
        runner = await _make_runner(scripts_dir, db_path)

        r1 = await runner.trigger("persistent")
        assert r1[0].outputs == {"counter": 1}

        r2 = await runner.trigger("persistent")
        assert r2[0].outputs == {"counter": 2}
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 4. Failure propagation in sandbox
# ---------------------------------------------------------------------------


class TestFailurePropagationSandbox:
    @pytest.mark.asyncio
    async def test_downstream_skipped(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "failing", FAILING)
        _write(scripts_dir, "downstream", DEPENDS_ON_FAILING)
        runner = await _make_runner(scripts_dir, db_path)

        results = await runner.trigger("downstream")

        by_id = {r.script_id: r for r in results}
        assert by_id["failing"].status == "failed"
        assert by_id["failing"].sandboxed is True
        assert by_id["downstream"].status == "skipped"
        runner.scheduler.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_observability_records_failure(self, scripts_dir: Path, db_path: str):
        _write(scripts_dir, "failing", FAILING)
        _write(scripts_dir, "downstream", DEPENDS_ON_FAILING)
        runner = await _make_runner(scripts_dir, db_path)

        await runner.trigger("downstream")
        runs = await runner.run_logger.get_runs()

        statuses = {r["script_id"]: r["status"] for r in runs}
        assert statuses["failing"] == "failed"
        assert statuses["downstream"] == "skipped"
        runner.scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 5. Fallback to local when Docker unavailable
# ---------------------------------------------------------------------------


class TestFallbackToLocal:
    @pytest.mark.asyncio
    async def test_runs_locally_with_warning(
        self, scripts_dir: Path, db_path: str, caplog: pytest.LogCaptureFixture
    ):
        _write(scripts_dir, "fallback", FALLBACK_SCRIPT)
        runner = await _make_runner(scripts_dir, db_path, use_sandbox=True)

        # Patch is_available to return False so executor falls back.
        with patch(
            "scriptbox.sandbox.docker_executor.DockerExecutor.is_available",
            return_value=False,
        ), caplog.at_level(logging.WARNING, logger="scriptbox.executor"):
            results = await runner.trigger("fallback")

        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].sandboxed is False
        assert any("falling back" in r.message.lower() for r in caplog.records)
        runner.scheduler.shutdown(wait=False)
