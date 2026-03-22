from __future__ import annotations

from pathlib import Path

import pytest

from scriptbox.executor import ExecutionResult, execute, execute_single
from scriptbox.loader import ScriptInfo, load_scripts

FIXTURES_DIR = Path(__file__).parent / "script_fixtures"


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "exec_test.db")


@pytest.fixture()
def all_scripts() -> list[ScriptInfo]:
    return load_scripts(FIXTURES_DIR)


def _pick(scripts: list[ScriptInfo], *ids: str) -> list[ScriptInfo]:
    """Return a subset of scripts matching the given ids."""
    id_set = set(ids)
    return [s for s in scripts if s.id in id_set]


def _result_by_id(results: list[ExecutionResult], script_id: str) -> ExecutionResult:
    return next(r for r in results if r.script_id == script_id)


# ---------------------------------------------------------------------------
# Standalone script
# ---------------------------------------------------------------------------


class TestStandaloneScript:
    @pytest.mark.asyncio
    async def test_success_status(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "pass_through")
        results = await execute(scripts, "pass_through", db_path)
        assert len(results) == 1
        assert results[0].status == "success"

    @pytest.mark.asyncio
    async def test_outputs(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "pass_through")
        results = await execute(scripts, "pass_through", db_path)
        assert results[0].outputs == {"data": "hello"}

    @pytest.mark.asyncio
    async def test_duration_is_nonnegative(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "pass_through")
        results = await execute(scripts, "pass_through", db_path)
        assert results[0].duration_ms >= 0

    @pytest.mark.asyncio
    async def test_error_is_none(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "pass_through")
        results = await execute(scripts, "pass_through", db_path)
        assert results[0].error is None


# ---------------------------------------------------------------------------
# Two-script chain: pass_through -> depends_on_pass
# ---------------------------------------------------------------------------


class TestTwoScriptChain:
    @pytest.mark.asyncio
    async def test_both_succeed(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "pass_through", "depends_on_pass")
        results = await execute(scripts, "depends_on_pass", db_path)
        assert len(results) == 2
        assert all(r.status == "success" for r in results)

    @pytest.mark.asyncio
    async def test_downstream_receives_upstream_output(
        self, all_scripts: list[ScriptInfo], db_path: str
    ):
        scripts = _pick(all_scripts, "pass_through", "depends_on_pass")
        results = await execute(scripts, "depends_on_pass", db_path)
        downstream = _result_by_id(results, "depends_on_pass")
        assert downstream.outputs == {"received": "hello"}

    @pytest.mark.asyncio
    async def test_upstream_runs_first(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "pass_through", "depends_on_pass")
        results = await execute(scripts, "depends_on_pass", db_path)
        ids = [r.script_id for r in results]
        assert ids.index("pass_through") < ids.index("depends_on_pass")


# ---------------------------------------------------------------------------
# Upstream failure → downstream skipped
# ---------------------------------------------------------------------------


class TestUpstreamFailure:
    @pytest.mark.asyncio
    async def test_failing_script_marked_failed(
        self, all_scripts: list[ScriptInfo], db_path: str
    ):
        scripts = _pick(all_scripts, "failing_script", "depends_on_failing")
        results = await execute(scripts, "depends_on_failing", db_path)
        failing = _result_by_id(results, "failing_script")
        assert failing.status == "failed"
        assert "intentional failure" in failing.error

    @pytest.mark.asyncio
    async def test_downstream_marked_skipped(
        self, all_scripts: list[ScriptInfo], db_path: str
    ):
        scripts = _pick(all_scripts, "failing_script", "depends_on_failing")
        results = await execute(scripts, "depends_on_failing", db_path)
        downstream = _result_by_id(results, "depends_on_failing")
        assert downstream.status == "skipped"

    @pytest.mark.asyncio
    async def test_skipped_has_error_message(
        self, all_scripts: list[ScriptInfo], db_path: str
    ):
        scripts = _pick(all_scripts, "failing_script", "depends_on_failing")
        results = await execute(scripts, "depends_on_failing", db_path)
        downstream = _result_by_id(results, "depends_on_failing")
        assert downstream.error is not None


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_slow_script_times_out(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "slow_script")
        results = await execute(scripts, "slow_script", db_path)
        assert results[0].status == "failed"

    @pytest.mark.asyncio
    async def test_timeout_error_message(self, all_scripts: list[ScriptInfo], db_path: str):
        scripts = _pick(all_scripts, "slow_script")
        results = await execute(scripts, "slow_script", db_path)
        assert "Timed out" in results[0].error


# ---------------------------------------------------------------------------
# execute_single
# ---------------------------------------------------------------------------


class TestExecuteSingle:
    @pytest.mark.asyncio
    async def test_basic(self, all_scripts: list[ScriptInfo], db_path: str):
        info = next(s for s in all_scripts if s.id == "pass_through")
        result = await execute_single(info, db_path)
        assert result.status == "success"
        assert result.outputs == {"data": "hello"}

    @pytest.mark.asyncio
    async def test_with_manual_inputs(self, all_scripts: list[ScriptInfo], db_path: str):
        info = next(s for s in all_scripts if s.id == "depends_on_pass")
        result = await execute_single(
            info, db_path, inputs={"pass_through": {"data": "manual"}}
        )
        assert result.status == "success"
        assert result.outputs == {"received": "manual"}
