"""Tests for DockerExecutor. Requires a running Docker daemon.

Run with:  pytest -m docker tests/test_docker_executor.py -v
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import docker
import pytest

from scriptbox.loader import ScriptInfo
from scriptbox.sandbox.config import SandboxConfig
from scriptbox.sandbox.docker_executor import DockerExecutor
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

_DUMMY_RUN_FN = None  # Not used – DockerExecutor runs via container harness.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_script(tmp_path: Path, name: str, source: str) -> Path:
    """Write a script file and return its path."""
    p = tmp_path / f"{name}.py"
    p.write_text(textwrap.dedent(source))
    return p


def _make_info(
    script_id: str,
    path: Path,
    meta: dict | None = None,
) -> ScriptInfo:
    meta = meta or {"name": script_id}
    return ScriptInfo(
        id=script_id,
        path=path,
        name=meta.get("name", script_id),
        meta=meta,
        run_fn=_DUMMY_RUN_FN,
    )


@pytest.fixture(scope="module", autouse=True)
def _ensure_image():
    """Build the runner image once for the entire module."""
    ImageBuilder().ensure_image()


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "docker_test.db")


# ---------------------------------------------------------------------------
# 1. Simple script returning outputs
# ---------------------------------------------------------------------------


class TestSimpleScript:
    @pytest.mark.asyncio
    async def test_returns_outputs(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "simple", """\
            META = {"name": "Simple"}

            async def run(ctx):
                return {"value": 42}
        """)
        info = _make_info("simple", path)
        de = DockerExecutor(db_path)
        result = await de.execute(info)

        assert result.status == "success"
        assert result.outputs == {"value": 42}
        assert result.error is None
        assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# 2. Store persists in the store directory
# ---------------------------------------------------------------------------


class TestStorePersists:
    @pytest.mark.asyncio
    async def test_store_file_created(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "writer", """\
            META = {"name": "Writer"}

            async def run(ctx):
                await ctx.store.set("color", "blue")
                return {"wrote": True}
        """)
        info = _make_info("writer", path)
        de = DockerExecutor(db_path)
        result = await de.execute(info)

        assert result.status == "success"
        store_dir = Path(db_path).parent / "stores" / "writer"
        assert store_dir.exists()
        # The store.db file should exist in the store directory.
        assert (store_dir / "store.db").exists()


# ---------------------------------------------------------------------------
# 3. Reads ctx.inputs
# ---------------------------------------------------------------------------


class TestInputs:
    @pytest.mark.asyncio
    async def test_receives_inputs(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "reader", """\
            META = {"name": "Reader"}

            async def run(ctx):
                val = ctx.inputs.get("upstream", {}).get("x")
                return {"got": val}
        """)
        info = _make_info("reader", path)
        de = DockerExecutor(db_path)
        result = await de.execute(info, inputs={"upstream": {"x": 99}})

        assert result.status == "success"
        assert result.outputs == {"got": 99}


# ---------------------------------------------------------------------------
# 4. Memory limit – container killed (not hang)
# ---------------------------------------------------------------------------


class TestMemoryLimit:
    @pytest.mark.asyncio
    async def test_oom_fails(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "oom", """\
            META = {"name": "OOM"}

            async def run(ctx):
                data = []
                while True:
                    data.append(b"x" * (10 * 1024 * 1024))
        """)
        info = _make_info("oom", path)
        cfg = SandboxConfig(memory="32m", timeout=15)
        de = DockerExecutor(db_path)
        result = await de.execute(info, sandbox_config=cfg)

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# 5. Timeout – container killed within ~timeout seconds
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_kills_container(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "sleeper", """\
            import asyncio

            META = {"name": "Sleeper"}

            async def run(ctx):
                await asyncio.sleep(60)
        """)
        info = _make_info("sleeper", path)
        cfg = SandboxConfig(timeout=2)
        de = DockerExecutor(db_path)

        import time
        t0 = time.monotonic()
        result = await de.execute(info, sandbox_config=cfg)
        elapsed = time.monotonic() - t0

        assert result.status == "failed"
        assert "Timed out" in result.error
        assert elapsed < 10  # should finish well under 10s


# ---------------------------------------------------------------------------
# 6. Network mode "none" – HTTP requests fail
# ---------------------------------------------------------------------------


class TestNetworkNone:
    @pytest.mark.asyncio
    async def test_no_network_fails_http(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "net_none", """\
            META = {"name": "NetNone"}

            async def run(ctx):
                resp = await ctx.http.get("https://httpbin.org/get")
                return {"status": resp.status_code}
        """)
        info = _make_info("net_none", path)
        cfg = SandboxConfig(network="none", timeout=15)
        de = DockerExecutor(db_path)
        result = await de.execute(info, sandbox_config=cfg)

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# 7. Default network – HTTP requests succeed
# ---------------------------------------------------------------------------


class TestNetworkBridge:
    @pytest.mark.asyncio
    async def test_bridge_allows_http(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "net_bridge", """\
            import httpx

            META = {"name": "NetBridge"}

            async def run(ctx):
                async with httpx.AsyncClient() as client:
                    resp = await client.get("https://httpbin.org/get")
                return {"status": resp.status_code}
        """)
        info = _make_info("net_bridge", path)
        cfg = SandboxConfig(network="bridge", timeout=30)
        de = DockerExecutor(db_path)
        result = await de.execute(info, sandbox_config=cfg)

        assert result.status == "success"
        assert result.outputs["status"] == 200


# ---------------------------------------------------------------------------
# 8. Store persists across two container executions
# ---------------------------------------------------------------------------


class TestStorePersistsAcrossRuns:
    @pytest.mark.asyncio
    async def test_second_run_reads_first_run_data(
        self, tmp_path: Path, db_path: str
    ):
        writer_src = """\
            META = {"name": "Persist"}

            async def run(ctx):
                await ctx.store.set("counter", 1)
                return {"wrote": 1}
        """
        reader_src = """\
            META = {"name": "Persist"}

            async def run(ctx):
                val = await ctx.store.get("counter", default=0)
                return {"read": val}
        """
        writer_path = _write_script(tmp_path, "persist_w", writer_src)
        reader_path = _write_script(tmp_path, "persist_r", reader_src)

        # Both use the same script_id so they share a store namespace.
        writer_info = _make_info("persist", writer_path)
        reader_info = _make_info("persist", reader_path)

        de = DockerExecutor(db_path)

        # First run: write
        r1 = await de.execute(writer_info)
        assert r1.status == "success"

        # Second run: read
        r2 = await de.execute(reader_info)
        assert r2.status == "success"
        assert r2.outputs == {"read": 1}
