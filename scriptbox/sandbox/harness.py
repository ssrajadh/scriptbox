"""Container entry-point for sandboxed script execution.

Reads configuration and a script from ``/run/``, executes it with a
:class:`ScriptContext`, and writes results back to ``/run/``.

The core logic lives in :func:`run_harness` which accepts a *base_path*
so it can be unit-tested outside Docker.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from types import ModuleType

import httpx

from scriptbox.context import ScriptContext, StubLLMClient, StubTelegramClient
from scriptbox.store import ScriptStore

_DEFAULT_DB_PATH = "/store/store.db"


def _read_json(path: Path) -> dict:
    """Read a JSON file, returning ``{}`` if the file is missing."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _import_script(path: Path) -> ModuleType:
    """Dynamically import a Python file as a module."""
    spec = importlib.util.spec_from_file_location("_user_script", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run_harness(base_path: str | Path) -> int:
    """Execute the harness logic rooted at *base_path*.

    Expected layout under *base_path*::

        config.json    – {"script_id": "...", "db_path": "..."}
        script.py      – the user script to execute
        inputs.json    – (optional) upstream outputs
        secrets.json   – (optional) script secrets

    Writes back::

        outputs.json   – return value of run()
        result.json    – execution metadata

    Returns 0 on success, 1 on failure.
    """
    base = Path(base_path)
    result_path = base / "result.json"
    outputs_path = base / "outputs.json"

    # ---- load configuration ------------------------------------------------
    config = _read_json(base / "config.json")
    script_id: str = config.get("script_id", "unknown")
    db_path: str = config.get("db_path", _DEFAULT_DB_PATH)

    inputs = _read_json(base / "inputs.json")
    secrets_file = base / "secrets.json"
    secrets_path_str = str(secrets_file) if secrets_file.exists() else None

    # ---- import the user script --------------------------------------------
    script_file = base / "script.py"
    try:
        module = _import_script(script_file)
    except Exception:
        tb = traceback.format_exc()
        _write_json(result_path, {"status": "failed", "duration_ms": 0, "error": tb})
        _write_json(outputs_path, {})
        return 1

    # ---- build context and run ---------------------------------------------
    http_client = httpx.AsyncClient()
    ctx = ScriptContext(
        script_id=script_id,
        db_path=db_path,
        inputs=inputs,
        secrets_path=secrets_path_str,
        http_client=http_client,
    )

    t0 = time.monotonic()
    try:
        output = await module.run(ctx)
    except Exception:
        duration_ms = int((time.monotonic() - t0) * 1000)
        tb = traceback.format_exc()
        _write_json(result_path, {"status": "failed", "duration_ms": duration_ms, "error": tb})
        _write_json(outputs_path, {})
        await http_client.aclose()
        return 1

    duration_ms = int((time.monotonic() - t0) * 1000)

    # ---- serialize outputs -------------------------------------------------
    try:
        outputs_serializable = output if isinstance(output, (dict, type(None))) else None
        _write_json(outputs_path, outputs_serializable)
    except (TypeError, ValueError):
        tb = traceback.format_exc()
        _write_json(result_path, {
            "status": "failed",
            "duration_ms": duration_ms,
            "error": f"Output not JSON-serializable: {tb}",
        })
        _write_json(outputs_path, {})
        await http_client.aclose()
        return 1

    _write_json(result_path, {"status": "success", "duration_ms": duration_ms, "error": None})
    await http_client.aclose()
    return 0


def _write_json(path: Path, data) -> None:
    """Write *data* as JSON to *path*, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def main() -> None:
    """Container entry-point."""
    exit_code = asyncio.run(run_harness("/run"))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
