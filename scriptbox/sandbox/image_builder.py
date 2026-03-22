from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path

import docker

logger = logging.getLogger(__name__)

IMAGE_TAG = "scriptbox-runner:latest"
_HASH_LABEL = "scriptbox.source_hash"

_RUNTIME_DEPS = [
    "httpx",
    "aiosqlite",
    "apscheduler",
]

_DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scriptbox/ ./scriptbox/
COPY harness.py .
ENTRYPOINT ["python", "harness.py"]
"""

_HARNESS_PY = """\
\"\"\"Container entry-point – delegates to scriptbox.sandbox.harness.\"\"\"
from scriptbox.sandbox.harness import main

if __name__ == "__main__":
    main()
"""


class ImageBuilder:
    """Builds and caches the Docker image used by script containers."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self._project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        self._client = docker.from_env()

    def image_exists(self) -> bool:
        """Return True if the runner image already exists locally."""
        try:
            self._client.images.get(IMAGE_TAG)
            return True
        except docker.errors.ImageNotFound:
            return False

    def ensure_image(self) -> str:
        """Build the image if it doesn't exist or sources changed. Return image tag."""
        return self.build(force=False)

    def build(self, force: bool = False) -> str:
        """Build the Docker image, returning the image tag.

        If *force* is False the build is skipped when the existing image's
        source-hash label matches the current source files.
        """
        current_hash = self._source_hash()

        if not force:
            try:
                image = self._client.images.get(IMAGE_TAG)
                stored_hash = image.labels.get(_HASH_LABEL)
                if stored_hash == current_hash:
                    logger.info("Image %s is up-to-date (hash %s)", IMAGE_TAG, current_hash[:12])
                    return IMAGE_TAG
            except docker.errors.ImageNotFound:
                pass

        logger.info("Building image %s (hash %s) ...", IMAGE_TAG, current_hash[:12])
        build_dir = self._assemble_context()
        try:
            self._client.images.build(
                path=str(build_dir),
                tag=IMAGE_TAG,
                labels={_HASH_LABEL: current_hash},
                rm=True,
            )
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)

        logger.info("Image %s built successfully", IMAGE_TAG)
        return IMAGE_TAG

    def cleanup_containers(self) -> int:
        """Remove any stopped containers created from the scriptbox image. Returns count removed."""
        removed = 0
        for c in self._client.containers.list(all=True, filters={"ancestor": IMAGE_TAG}):
            if c.status in ("exited", "dead", "created"):
                c.remove(force=True)
                removed += 1
                logger.info("Removed container %s", c.short_id)
        return removed

    def cleanup_image(self) -> bool:
        """Remove the scriptbox-runner image. Returns True if removed."""
        try:
            self._client.images.remove(IMAGE_TAG, force=True)
            logger.info("Removed image %s", IMAGE_TAG)
            return True
        except docker.errors.ImageNotFound:
            return False

    def get_image_info(self) -> dict:
        """Return image size, creation date, and source hash (or empty dict if not found)."""
        try:
            image = self._client.images.get(IMAGE_TAG)
        except docker.errors.ImageNotFound:
            return {}
        return {
            "tag": IMAGE_TAG,
            "size_mb": round(image.attrs["Size"] / (1024 * 1024), 1),
            "created": image.attrs.get("Created", ""),
            "source_hash": image.labels.get(_HASH_LABEL, ""),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assemble_context(self) -> Path:
        """Create a temporary directory with the Docker build context."""
        build_dir = Path(tempfile.mkdtemp(prefix="scriptbox-build-"))

        # Dockerfile
        (build_dir / "Dockerfile").write_text(_DOCKERFILE)

        # requirements.txt
        (build_dir / "requirements.txt").write_text(
            "\n".join(_RUNTIME_DEPS) + "\n"
        )

        # scriptbox package
        src = self._project_root / "scriptbox"
        dst = build_dir / "scriptbox"
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

        # harness.py
        harness_src = self._project_root / "harness.py"
        if harness_src.exists():
            shutil.copy2(harness_src, build_dir / "harness.py")
        else:
            (build_dir / "harness.py").write_text(_HARNESS_PY)

        return build_dir

    def _source_hash(self) -> str:
        """Compute a deterministic hash of all files that go into the image."""
        h = hashlib.sha256()

        # Hash Dockerfile template and requirements
        h.update(_DOCKERFILE.encode())
        h.update("\n".join(_RUNTIME_DEPS).encode())

        # Hash every file in the scriptbox package
        src = self._project_root / "scriptbox"
        if src.is_dir():
            for p in sorted(src.rglob("*.py")):
                h.update(p.relative_to(self._project_root).as_posix().encode())
                h.update(p.read_bytes())

        # Hash harness.py if it exists on disk, otherwise the embedded default
        harness_src = self._project_root / "harness.py"
        if harness_src.exists():
            h.update(harness_src.read_bytes())
        else:
            h.update(_HARNESS_PY.encode())

        return h.hexdigest()
