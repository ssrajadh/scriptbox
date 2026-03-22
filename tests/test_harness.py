"""Unit tests for the container harness logic (no Docker required)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scriptbox.sandbox.harness import run_harness

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_SCRIPT = """\
META = {"name": "Good"}

async def run(ctx):
    return {"answer": 42}
"""

_FAILING_SCRIPT = """\
META = {"name": "Failing"}

async def run(ctx):
    raise ValueError("boom")
"""

_NONE_SCRIPT = """\
META = {"name": "None returner"}

async def run(ctx):
    pass
"""

_IMPORT_ERROR_SCRIPT = """\
raise SyntaxError("bad code")
"""

_BAD_OUTPUT_SCRIPT = """\
import datetime

META = {"name": "Bad output"}

async def run(ctx):
    return {"ts": datetime.datetime.now()}
"""


def _setup_run_dir(
    tmp_path: Path,
    script_src: str,
    *,
    inputs: dict | None = None,
    env_vars: dict[str, str] | None = None,
    config: dict | None = None,
) -> Path:
    """Populate a run directory and return its path."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    (run_dir / "script.py").write_text(script_src)
    (run_dir / "config.json").write_text(
        json.dumps(config or {"script_id": "test_script", "db_path": str(tmp_path / "store.db")})
    )
    if inputs is not None:
        (run_dir / "inputs.json").write_text(json.dumps(inputs))
    if env_vars is not None:
        lines = [f"{k}={v}" for k, v in env_vars.items()]
        (run_dir / ".env").write_text("\n".join(lines) + "\n")

    return run_dir


# ---------------------------------------------------------------------------
# Successful script
# ---------------------------------------------------------------------------


class TestSuccessfulScript:
    @pytest.mark.asyncio
    async def test_exit_code_zero(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _GOOD_SCRIPT)
        rc = await run_harness(run_dir)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_outputs_json(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _GOOD_SCRIPT)
        await run_harness(run_dir)
        outputs = json.loads((run_dir / "outputs.json").read_text())
        assert outputs == {"answer": 42}

    @pytest.mark.asyncio
    async def test_result_json(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _GOOD_SCRIPT)
        await run_harness(run_dir)
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "success"
        assert result["error"] is None
        assert isinstance(result["duration_ms"], int)
        assert result["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Failing script
# ---------------------------------------------------------------------------


class TestFailingScript:
    @pytest.mark.asyncio
    async def test_exit_code_one(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _FAILING_SCRIPT)
        rc = await run_harness(run_dir)
        assert rc == 1

    @pytest.mark.asyncio
    async def test_result_json_failed(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _FAILING_SCRIPT)
        await run_harness(run_dir)
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "failed"
        assert "boom" in result["error"]
        assert isinstance(result["duration_ms"], int)

    @pytest.mark.asyncio
    async def test_outputs_empty_on_failure(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _FAILING_SCRIPT)
        await run_harness(run_dir)
        outputs = json.loads((run_dir / "outputs.json").read_text())
        assert outputs == {}


# ---------------------------------------------------------------------------
# Script returning None
# ---------------------------------------------------------------------------


class TestNoneReturn:
    @pytest.mark.asyncio
    async def test_exit_code_zero(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _NONE_SCRIPT)
        rc = await run_harness(run_dir)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_outputs_null(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _NONE_SCRIPT)
        await run_harness(run_dir)
        raw = (run_dir / "outputs.json").read_text()
        assert json.loads(raw) is None

    @pytest.mark.asyncio
    async def test_result_success(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _NONE_SCRIPT)
        await run_harness(run_dir)
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Missing inputs.json → defaults to {}
# ---------------------------------------------------------------------------


class TestMissingInputs:
    @pytest.mark.asyncio
    async def test_runs_successfully(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _GOOD_SCRIPT)
        # inputs.json is not written by _setup_run_dir when inputs=None
        assert not (run_dir / "inputs.json").exists()
        rc = await run_harness(run_dir)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_outputs_correct(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _GOOD_SCRIPT)
        await run_harness(run_dir)
        outputs = json.loads((run_dir / "outputs.json").read_text())
        assert outputs == {"answer": 42}


# ---------------------------------------------------------------------------
# Script import error
# ---------------------------------------------------------------------------


class TestImportError:
    @pytest.mark.asyncio
    async def test_exit_code_one(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _IMPORT_ERROR_SCRIPT)
        rc = await run_harness(run_dir)
        assert rc == 1

    @pytest.mark.asyncio
    async def test_result_has_error(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _IMPORT_ERROR_SCRIPT)
        await run_harness(run_dir)
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "failed"
        assert "SyntaxError" in result["error"]


# ---------------------------------------------------------------------------
# Non-JSON-serializable output
# ---------------------------------------------------------------------------


class TestNonSerializableOutput:
    @pytest.mark.asyncio
    async def test_exit_code_one(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _BAD_OUTPUT_SCRIPT)
        rc = await run_harness(run_dir)
        assert rc == 1

    @pytest.mark.asyncio
    async def test_result_has_serialization_error(self, tmp_path: Path):
        run_dir = _setup_run_dir(tmp_path, _BAD_OUTPUT_SCRIPT)
        await run_harness(run_dir)
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "failed"
        assert "not JSON-serializable" in result["error"]


# ---------------------------------------------------------------------------
# Inputs and secrets are passed through
# ---------------------------------------------------------------------------


class TestInputsAndSecrets:
    @pytest.mark.asyncio
    async def test_inputs_available(self, tmp_path: Path):
        script = """\
META = {"name": "Reader"}

async def run(ctx):
    return {"got": ctx.inputs.get("upstream", {}).get("val")}
"""
        run_dir = _setup_run_dir(
            tmp_path, script, inputs={"upstream": {"val": "hello"}}
        )
        await run_harness(run_dir)
        outputs = json.loads((run_dir / "outputs.json").read_text())
        assert outputs == {"got": "hello"}

    @pytest.mark.asyncio
    async def test_secrets_available(self, tmp_path: Path):
        script = """\
META = {"name": "SecretReader"}

async def run(ctx):
    return {"key": ctx.secrets.get("API_KEY")}
"""
        run_dir = _setup_run_dir(
            tmp_path, script, env_vars={"API_KEY": "abc123"}
        )
        await run_harness(run_dir)
        outputs = json.loads((run_dir / "outputs.json").read_text())
        assert outputs == {"key": "abc123"}
