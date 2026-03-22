"""Security tests for the Docker sandbox.

Verify that containers cannot escape their isolation boundaries.

Run with:  pytest -m docker tests/test_sandbox_security.py -v
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_script(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / f"{name}.py"
    p.write_text(textwrap.dedent(source))
    return p


def _make_info(script_id: str, path: Path, meta: dict | None = None) -> ScriptInfo:
    meta = meta or {"name": script_id}
    return ScriptInfo(
        id=script_id, path=path, name=meta.get("name", script_id),
        meta=meta, run_fn=None,
    )


@pytest.fixture(scope="module", autouse=True)
def _ensure_image():
    ImageBuilder().ensure_image()


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "sec_test.db")


# ---------------------------------------------------------------------------
# 1. Host filesystem read isolation
# ---------------------------------------------------------------------------


class TestHostFilesystemRead:
    @pytest.mark.asyncio
    async def test_etc_passwd_is_container_not_host(
        self, tmp_path: Path, db_path: str
    ):
        path = _write_script(tmp_path, "fs_read", """\
            META = {"name": "FS Read"}

            async def run(ctx):
                with open("/etc/passwd") as f:
                    return {"content": f.read()}
        """)
        info = _make_info("fs_read", path)
        de = DockerExecutor(db_path)
        result = await de.execute(info)

        assert result.status == "success"
        host_passwd = Path("/etc/passwd").read_text()
        # Container's /etc/passwd is from python:3.12-slim, not the host.
        assert result.outputs["content"] != host_passwd


# ---------------------------------------------------------------------------
# 2. Host filesystem write isolation
# ---------------------------------------------------------------------------


class TestHostFilesystemWrite:
    @pytest.mark.asyncio
    async def test_write_does_not_escape(self, tmp_path: Path, db_path: str):
        # Container's root is read-only; /tmp is a tmpfs inside the container.
        # Writing to /tmp/escape_test inside the container must NOT appear on host.
        path = _write_script(tmp_path, "fs_write", """\
            import pathlib
            META = {"name": "FS Write"}

            async def run(ctx):
                pathlib.Path("/tmp/escape_test").write_text("pwned")
                return {"wrote": True}
        """)
        info = _make_info("fs_write", path)
        de = DockerExecutor(db_path)
        result = await de.execute(info)

        assert result.status == "success"
        assert not Path("/tmp/escape_test").exists()


# ---------------------------------------------------------------------------
# 3. Store namespace isolation
# ---------------------------------------------------------------------------


class TestStoreIsolation:
    @pytest.mark.asyncio
    async def test_different_scripts_cannot_read_each_others_store(
        self, tmp_path: Path, db_path: str
    ):
        writer_path = _write_script(tmp_path, "store_writer", """\
            META = {"name": "Writer"}

            async def run(ctx):
                await ctx.store.set("secret", "script_a_data")
                return {"wrote": True}
        """)
        reader_path = _write_script(tmp_path, "store_reader", """\
            META = {"name": "Reader"}

            async def run(ctx):
                val = await ctx.store.get("secret")
                return {"got": val}
        """)

        de = DockerExecutor(db_path)

        # Script A writes under namespace "script_a"
        info_a = _make_info("script_a", writer_path)
        r1 = await de.execute(info_a)
        assert r1.status == "success"

        # Script B reads under namespace "script_b" — should get None
        info_b = _make_info("script_b", reader_path)
        r2 = await de.execute(info_b)
        assert r2.status == "success"
        assert r2.outputs["got"] is None


# ---------------------------------------------------------------------------
# 4. Memory resource bomb
# ---------------------------------------------------------------------------


class TestResourceBomb:
    @pytest.mark.asyncio
    async def test_oom_killed(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "mem_bomb", """\
            META = {"name": "Mem Bomb"}

            async def run(ctx):
                data = []
                while True:
                    data.append(b"x" * (10 * 1024 * 1024))
        """)
        info = _make_info("mem_bomb", path)
        cfg = SandboxConfig(memory="128m", timeout=15)
        de = DockerExecutor(db_path)
        result = await de.execute(info, sandbox_config=cfg)

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# 5. Fork bomb (pids_limit)
# ---------------------------------------------------------------------------


class TestForkBomb:
    @pytest.mark.asyncio
    async def test_fork_bomb_contained(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "fork_bomb", """\
            import subprocess, sys
            META = {"name": "Fork Bomb"}

            async def run(ctx):
                procs = []
                for _ in range(200):
                    procs.append(subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(60)"]
                    ))
                return {"spawned": len(procs)}
        """)
        info = _make_info("fork_bomb", path)
        cfg = SandboxConfig(pids_limit=32, timeout=10)
        de = DockerExecutor(db_path)
        result = await de.execute(info, sandbox_config=cfg)

        # Must fail (OOM on PIDs or error) — must NOT hang.
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# 6. Network none — HTTP request fails
# ---------------------------------------------------------------------------


class TestNetworkNoneEscape:
    @pytest.mark.asyncio
    async def test_http_request_fails(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "net_escape", """\
            import httpx
            META = {"name": "Net Escape"}

            async def run(ctx):
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get("https://httpbin.org/get")
                return {"status": resp.status_code}
        """)
        info = _make_info("net_escape", path)
        cfg = SandboxConfig(network="none", timeout=15)
        de = DockerExecutor(db_path)
        result = await de.execute(info, sandbox_config=cfg)

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# 7. Run directory contents — no host file leakage
# ---------------------------------------------------------------------------


class TestRunDirContents:
    @pytest.mark.asyncio
    async def test_only_expected_files(self, tmp_path: Path, db_path: str):
        path = _write_script(tmp_path, "dir_list", """\
            import os
            META = {"name": "Dir List"}

            async def run(ctx):
                files = sorted(os.listdir("/run"))
                return {"files": files}
        """)
        info = _make_info("dir_list", path)
        de = DockerExecutor(db_path)
        result = await de.execute(info)

        assert result.status == "success"
        files = set(result.outputs["files"])
        # The 4 seed files must be present.
        required = {"config.json", "inputs.json", "script.py"}
        assert required.issubset(files)
        # Only harmless Python artifacts (__pycache__) may appear alongside
        # them — no host files should leak in.
        extra = files - required
        assert extra.issubset({"__pycache__"})
