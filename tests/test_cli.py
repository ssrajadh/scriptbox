"""Tests for the scriptbox CLI (__main__.py)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scriptbox.__main__ import main


def _write(d: Path, name: str, src: str) -> Path:
    p = d / f"{name}.py"
    p.write_text(src)
    return p


SIMPLE_SCRIPT = """\
META = {"name": "Simple"}

async def run(ctx):
    return {"ok": True}
"""


@pytest.fixture()
def scripts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "cli_test.db")


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    @pytest.mark.docker
    def test_check_shows_docker_available(self, capsys: pytest.CaptureFixture[str]):
        with patch("sys.argv", ["scriptbox", "check"]):
            main()
        out = capsys.readouterr().out
        assert "Docker:" in out or "docker" in out.lower()

    def test_check_no_docker(self, capsys: pytest.CaptureFixture[str]):
        with (
            patch("sys.argv", ["scriptbox", "check"]),
            patch("scriptbox.sandbox.image_builder.ImageBuilder") as mock_cls,
        ):
            mock_builder = mock_cls.return_value
            mock_builder.image_exists.return_value = False
            mock_builder.get_image_info.return_value = {}
            main()
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "not available" in out.lower()


# ---------------------------------------------------------------------------
# trigger command
# ---------------------------------------------------------------------------


class TestTriggerCommand:
    def test_trigger_success(
        self, scripts_dir: Path, db_path: str, capsys: pytest.CaptureFixture[str]
    ):
        _write(scripts_dir, "simple", SIMPLE_SCRIPT)
        with patch(
            "sys.argv",
            ["scriptbox", "trigger", "simple", "--scripts-dir", str(scripts_dir), "--db", db_path],
        ):
            main()
        out = capsys.readouterr().out
        assert "OK" in out
        assert "simple" in out

    def test_trigger_nonexistent(
        self, scripts_dir: Path, db_path: str
    ):
        _write(scripts_dir, "simple", SIMPLE_SCRIPT)
        with (
            patch(
                "sys.argv",
                [
                    "scriptbox",
                    "trigger",
                    "nonexistent",
                    "--scripts-dir",
                    str(scripts_dir),
                    "--db",
                    db_path,
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# no command
# ---------------------------------------------------------------------------


class TestNoCommand:
    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]):
        with (
            patch("sys.argv", ["scriptbox"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0
