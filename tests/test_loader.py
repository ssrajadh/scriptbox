from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from scriptbox.loader import ScriptInfo, load_scripts

FIXTURES_DIR = Path(__file__).parent / "script_fixtures"


# ---------------------------------------------------------------------------
# Loading a mixed directory
# ---------------------------------------------------------------------------


class TestLoadScriptsMixedDirectory:
    """Load the fixtures dir which contains both valid and invalid scripts."""

    @pytest.fixture()
    def loaded(self) -> list[ScriptInfo]:
        return load_scripts(FIXTURES_DIR)

    def test_only_valid_scripts_returned(self, loaded: list[ScriptInfo]):
        ids = {s.id for s in loaded}
        # Must include the original two plus the executor fixture scripts
        expected = {
            "valid_basic",
            "valid_with_deps",
            "pass_through",
            "depends_on_pass",
            "failing_script",
            "depends_on_failing",
            "slow_script",
        }
        assert ids == expected

    def test_invalid_scripts_excluded(self, loaded: list[ScriptInfo]):
        ids = {s.id for s in loaded}
        for bad in ("invalid_no_meta", "invalid_no_run", "invalid_meta_type", "valid_sync_run"):
            assert bad not in ids

    def test_returns_list(self, loaded: list[ScriptInfo]):
        assert isinstance(loaded, list)

    def test_count(self, loaded: list[ScriptInfo]):
        assert len(loaded) == 7


# ---------------------------------------------------------------------------
# ScriptInfo fields for valid_basic
# ---------------------------------------------------------------------------


class TestValidBasicFields:
    @pytest.fixture()
    def info(self) -> ScriptInfo:
        scripts = load_scripts(FIXTURES_DIR)
        return next(s for s in scripts if s.id == "valid_basic")

    def test_id(self, info: ScriptInfo):
        assert info.id == "valid_basic"

    def test_path_exists(self, info: ScriptInfo):
        assert info.path.exists()
        assert info.path.name == "valid_basic.py"

    def test_name_from_meta(self, info: ScriptInfo):
        assert info.name == "Basic Script"

    def test_meta_is_dict(self, info: ScriptInfo):
        assert isinstance(info.meta, dict)
        assert info.meta["name"] == "Basic Script"

    def test_run_fn_is_coroutine_function(self, info: ScriptInfo):
        assert inspect.iscoroutinefunction(info.run_fn)

    @pytest.mark.asyncio
    async def test_run_fn_callable(self, info: ScriptInfo):
        result = await info.run_fn(None)
        assert result == "ok"


# ---------------------------------------------------------------------------
# ScriptInfo fields for valid_with_deps
# ---------------------------------------------------------------------------


class TestValidWithDepsFields:
    @pytest.fixture()
    def info(self) -> ScriptInfo:
        scripts = load_scripts(FIXTURES_DIR)
        return next(s for s in scripts if s.id == "valid_with_deps")

    def test_name(self, info: ScriptInfo):
        assert info.name == "Script With Deps"

    def test_depends_on(self, info: ScriptInfo):
        assert info.meta["depends_on"] == ["valid_basic"]

    def test_outputs(self, info: ScriptInfo):
        assert info.meta["outputs"] == ["report.json"]

    def test_schedule(self, info: ScriptInfo):
        assert info.meta["schedule"] == "*/5 * * * *"

    def test_description(self, info: ScriptInfo):
        assert info.meta["description"] == "A script that depends on another"

    def test_sandbox(self, info: ScriptInfo):
        assert info.meta["sandbox"] == {"network": False}


# ---------------------------------------------------------------------------
# Warning / skip behaviour
# ---------------------------------------------------------------------------


class TestWarningsOnInvalidScripts:
    def test_no_meta_warns(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="scriptbox.loader"):
            load_scripts(FIXTURES_DIR)
        assert any("invalid_no_meta" in r.message and "META" in r.message for r in caplog.records)

    def test_no_run_warns(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="scriptbox.loader"):
            load_scripts(FIXTURES_DIR)
        assert any("invalid_no_run" in r.message and "run" in r.message for r in caplog.records)

    def test_bad_meta_type_warns(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="scriptbox.loader"):
            load_scripts(FIXTURES_DIR)
        assert any("invalid_meta_type" in r.message and "str" in r.message for r in caplog.records)

    def test_sync_run_warns(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="scriptbox.loader"):
            load_scripts(FIXTURES_DIR)
        assert any("valid_sync_run" in r.message and "async" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_nonexistent_directory(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="scriptbox.loader"):
            result = load_scripts("/tmp/does_not_exist_scriptbox")
        assert result == []

    def test_empty_directory(self, tmp_path: Path):
        result = load_scripts(tmp_path)
        assert result == []

    def test_script_info_is_frozen(self):
        scripts = load_scripts(FIXTURES_DIR)
        info = scripts[0]
        with pytest.raises(AttributeError):
            info.id = "hacked"
