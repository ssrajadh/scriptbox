from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from scriptbox.context import ScriptContext
from scriptbox.dag import build_graph, subgraph
from scriptbox.loader import ScriptInfo
from scriptbox.sandbox.config import SandboxConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


@dataclass
class ExecutionResult:
    script_id: str
    status: str  # "success", "failed", "skipped"
    duration_ms: int
    error: Optional[str]
    outputs: Optional[dict]
    sandboxed: bool = False


async def execute(
    scripts: list[ScriptInfo],
    target: str,
    db_path: str,
    secrets_path: str | None = None,
    use_sandbox: bool = False,
) -> list[ExecutionResult]:
    """Resolve the DAG for *target* and run scripts in topological order."""
    docker_exec = None
    if use_sandbox:
        docker_exec = _get_docker_executor(db_path, secrets_path)

    order = subgraph(scripts, target)
    graph = build_graph(scripts)
    scripts_by_id = {s.id: s for s in scripts}

    results: list[ExecutionResult] = []
    collected_outputs: dict[str, dict] = {}
    failed_ids: set[str] = set()

    for script_id in order:
        # Check if any dependency failed — if so, skip.
        deps = graph.get(script_id, [])
        if failed_ids.intersection(deps):
            results.append(
                ExecutionResult(
                    script_id=script_id,
                    status="skipped",
                    duration_ms=0,
                    error="upstream dependency failed",
                    outputs=None,
                    sandboxed=False,
                )
            )
            failed_ids.add(script_id)
            continue

        info = scripts_by_id[script_id]
        inputs = {dep: collected_outputs[dep] for dep in deps if dep in collected_outputs}
        sandbox_cfg = SandboxConfig.from_meta(info.meta)

        if docker_exec is not None and sandbox_cfg.enabled:
            result = await docker_exec.execute(info, inputs, sandbox_cfg)
            result.sandboxed = True
            logger.info("Script %s ran in Docker sandbox", script_id)
        else:
            result = await _run_local(info, db_path, inputs, secrets_path, sandbox_cfg.timeout)
            result.sandboxed = False
            logger.info("Script %s ran locally", script_id)

        results.append(result)

        if result.status == "success" and result.outputs is not None:
            collected_outputs[script_id] = result.outputs
        elif result.status == "failed":
            failed_ids.add(script_id)

    return results


async def execute_single(
    script_info: ScriptInfo,
    db_path: str,
    inputs: dict[str, Any] | None = None,
    secrets_path: str | None = None,
    use_sandbox: bool = False,
) -> ExecutionResult:
    """Run a single script without DAG resolution."""
    sandbox_cfg = SandboxConfig.from_meta(script_info.meta)

    if use_sandbox and sandbox_cfg.enabled:
        docker_exec = _get_docker_executor(db_path, secrets_path)
        if docker_exec is not None:
            result = await docker_exec.execute(script_info, inputs, sandbox_cfg)
            result.sandboxed = True
            return result

    result = await _run_local(
        script_info, db_path, inputs or {}, secrets_path, sandbox_cfg.timeout
    )
    result.sandboxed = False
    return result


def _get_docker_executor(db_path: str, secrets_path: str | None):
    """Return a DockerExecutor if Docker is available, else None with a warning."""
    try:
        from scriptbox.sandbox.docker_executor import DockerExecutor

        de = DockerExecutor(db_path=db_path, secrets_path=secrets_path)
        if de.is_available():
            return de
        logger.warning("Docker requested but daemon not available — falling back to local execution")
    except Exception:
        logger.warning("Docker SDK not available — falling back to local execution", exc_info=True)
    return None


async def _run_local(
    info: ScriptInfo,
    db_path: str,
    inputs: dict[str, Any],
    secrets_path: str | None,
    timeout: float,
) -> ExecutionResult:
    """Execute a script in-process (no container)."""
    ctx = ScriptContext(
        script_id=info.id,
        db_path=db_path,
        inputs=inputs,
        secrets_path=secrets_path,
    )
    t0 = time.monotonic()
    try:
        output = await asyncio.wait_for(info.run_fn(ctx), timeout=timeout)
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("Script %s timed out after %ds", info.id, timeout)
        return ExecutionResult(
            script_id=info.id,
            status="failed",
            duration_ms=duration_ms,
            error=f"Timed out after {timeout}s",
            outputs=None,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("Script %s failed: %s", info.id, exc)
        return ExecutionResult(
            script_id=info.id,
            status="failed",
            duration_ms=duration_ms,
            error=str(exc),
            outputs=None,
        )
    else:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return ExecutionResult(
            script_id=info.id,
            status="success",
            duration_ms=duration_ms,
            error=None,
            outputs=output if isinstance(output, dict) else None,
        )
