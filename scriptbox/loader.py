from __future__ import annotations

import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScriptInfo:
    id: str
    path: Path
    name: str
    meta: dict[str, Any]
    run_fn: Callable[..., Coroutine]


def load_scripts(scripts_dir: str | Path) -> list[ScriptInfo]:
    """Discover and validate Python scripts in *scripts_dir*.

    Each valid script must expose:
      - ``META``: a dict with at least a ``"name"`` key (str).
      - ``run``:  an async function accepting one positional argument.

    Scripts that fail validation are logged and skipped.
    """
    scripts_dir = Path(scripts_dir)
    if not scripts_dir.is_dir():
        logger.warning("Scripts directory does not exist: %s", scripts_dir)
        return []

    results: list[ScriptInfo] = []

    for py_file in sorted(scripts_dir.glob("*.py")):
        script_id = py_file.stem
        try:
            module = _import_file(py_file)
        except Exception:
            logger.warning("Failed to import %s", py_file, exc_info=True)
            continue

        meta = getattr(module, "META", None)
        if meta is None:
            logger.warning("Script %s is missing META dict – skipped", script_id)
            continue
        if not isinstance(meta, dict):
            logger.warning(
                "Script %s has META of type %s instead of dict – skipped",
                script_id,
                type(meta).__name__,
            )
            continue
        if "name" not in meta or not isinstance(meta["name"], str):
            logger.warning(
                "Script %s META is missing a 'name' string – skipped", script_id
            )
            continue

        run_fn = getattr(module, "run", None)
        if run_fn is None:
            logger.warning("Script %s has no run() function – skipped", script_id)
            continue
        if not callable(run_fn):
            logger.warning("Script %s run is not callable – skipped", script_id)
            continue
        if not inspect.iscoroutinefunction(run_fn):
            logger.warning(
                "Script %s run() is not async – skipped", script_id
            )
            continue

        # Validate run() accepts at least one positional arg (beyond *args/**kwargs).
        sig = inspect.signature(run_fn)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        if len(params) < 1:
            logger.warning(
                "Script %s run() must accept at least one argument – skipped",
                script_id,
            )
            continue

        results.append(
            ScriptInfo(
                id=script_id,
                path=py_file.resolve(),
                name=meta["name"],
                meta=meta,
                run_fn=run_fn,
            )
        )

    return results


def _import_file(path: Path):
    """Import a Python file as a module without adding it to sys.modules."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
