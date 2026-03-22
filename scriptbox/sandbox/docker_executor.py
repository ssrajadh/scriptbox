from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import docker
import docker.errors

from scriptbox.executor import ExecutionResult
from scriptbox.loader import ScriptInfo
from scriptbox.sandbox.config import SandboxConfig
from scriptbox.sandbox.image_builder import IMAGE_TAG, ImageBuilder

logger = logging.getLogger(__name__)


class DockerExecutor:
    """Runs scripts inside Docker containers using the sandbox harness."""

    def __init__(
        self,
        db_path: str,
        secrets_path: str | None = None,
        scripts_dir: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._secrets_path = secrets_path
        self._scripts_dir = scripts_dir
        self._image_builder = ImageBuilder()
        self._client = docker.from_env()

    def is_available(self) -> bool:
        """Return True if the Docker daemon is reachable."""
        try:
            self._client.ping()
            return True
        except Exception:
            return False

    async def execute(
        self,
        script_info: ScriptInfo,
        inputs: dict[str, Any] | None = None,
        sandbox_config: SandboxConfig | None = None,
    ) -> ExecutionResult:
        """Run *script_info* in a Docker container and return the result."""
        if sandbox_config is None:
            sandbox_config = SandboxConfig.from_meta(script_info.meta)

        loop = asyncio.get_event_loop()

        # Ensure image (blocking call pushed to thread).
        await loop.run_in_executor(None, self._image_builder.ensure_image)

        run_dir = Path(tempfile.mkdtemp(prefix="scriptbox-run-"))
        store_dir = Path(self._db_path).parent / "stores" / script_info.id
        store_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._prepare_run_dir(run_dir, script_info, inputs or {})
            container = await loop.run_in_executor(
                None,
                lambda: self._create_container(run_dir, store_dir, sandbox_config),
            )

            try:
                result = await self._wait_and_collect(
                    container, run_dir, script_info.id, sandbox_config.timeout
                )
            except Exception:
                self._force_remove(container)
                raise

            return result
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _prepare_run_dir(
        self,
        run_dir: Path,
        script_info: ScriptInfo,
        inputs: dict[str, Any],
    ) -> None:
        shutil.copy2(script_info.path, run_dir / "script.py")
        (run_dir / "config.json").write_text(
            json.dumps({"script_id": script_info.id})
        )
        (run_dir / "inputs.json").write_text(json.dumps(inputs))
        if self._secrets_path and Path(self._secrets_path).exists():
            shutil.copy2(self._secrets_path, run_dir / "secrets.json")
        else:
            (run_dir / "secrets.json").write_text("{}")

    def _create_container(
        self,
        run_dir: Path,
        store_dir: Path,
        cfg: SandboxConfig,
    ):
        """Create and start a detached container."""
        kwargs: dict[str, Any] = {
            "image": IMAGE_TAG,
            "volumes": {
                str(run_dir): {"bind": "/run", "mode": "rw"},
                str(store_dir): {"bind": "/store", "mode": "rw"},
            },
            "mem_limit": cfg.memory,
            "nano_cpus": int(cfg.cpu * 1e9),
            "detach": True,
            "environment": cfg.env or {},
        }

        if cfg.network == "none":
            kwargs["network_mode"] = "none"

        if cfg.read_only_root:
            kwargs["read_only"] = True
            kwargs["tmpfs"] = {"/tmp": "size=64m"}

        return self._client.containers.run(**kwargs)

    async def _wait_and_collect(
        self,
        container,
        run_dir: Path,
        script_id: str,
        timeout: int,
    ) -> ExecutionResult:
        """Wait for *container* to exit, then read results from *run_dir*."""
        loop = asyncio.get_event_loop()

        timed_out = False
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, container.wait),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            await loop.run_in_executor(None, lambda: container.kill())

        # Always remove the container.
        self._force_remove(container)

        if timed_out:
            return ExecutionResult(
                script_id=script_id,
                status="failed",
                duration_ms=timeout * 1000,
                error=f"Timed out after {timeout}s",
                outputs=None,
            )

        return self._read_results(run_dir, script_id)

    def _read_results(self, run_dir: Path, script_id: str) -> ExecutionResult:
        """Parse result.json and outputs.json written by the harness."""
        result_file = run_dir / "result.json"
        outputs_file = run_dir / "outputs.json"

        if not result_file.exists():
            return ExecutionResult(
                script_id=script_id,
                status="failed",
                duration_ms=0,
                error="Container produced no result.json",
                outputs=None,
            )

        result_data = json.loads(result_file.read_text())
        outputs = None
        if outputs_file.exists():
            raw = json.loads(outputs_file.read_text())
            if isinstance(raw, dict):
                outputs = raw

        return ExecutionResult(
            script_id=script_id,
            status=result_data.get("status", "failed"),
            duration_ms=result_data.get("duration_ms", 0),
            error=result_data.get("error"),
            outputs=outputs,
        )

    def _force_remove(self, container) -> None:
        try:
            container.remove(force=True)
        except Exception:
            pass
