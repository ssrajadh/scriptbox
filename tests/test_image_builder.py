"""Tests for ImageBuilder. Requires a running Docker daemon.

Run with:  pytest -m docker tests/test_image_builder.py -v
"""
from __future__ import annotations

import docker
import pytest

from scriptbox.sandbox.image_builder import IMAGE_TAG, ImageBuilder, _HASH_LABEL

# Skip every test in this module if Docker is not reachable.
pytestmark = pytest.mark.docker


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


if not _docker_available():
    pytest.skip("Docker daemon not available", allow_module_level=True)


@pytest.fixture(scope="module")
def builder() -> ImageBuilder:
    return ImageBuilder()


@pytest.fixture(scope="module")
def client() -> docker.DockerClient:
    return docker.from_env()


def _remove_image(client: docker.DockerClient) -> None:
    """Best-effort removal of the runner image."""
    try:
        client.images.remove(IMAGE_TAG, force=True)
    except docker.errors.ImageNotFound:
        pass


# ---------------------------------------------------------------------------
# image_exists() — before any build
# ---------------------------------------------------------------------------


class TestImageExistsBeforeBuild:
    def test_false_when_no_image(self, builder: ImageBuilder, client: docker.DockerClient):
        _remove_image(client)
        assert builder.image_exists() is False


# ---------------------------------------------------------------------------
# ensure_image() — first call builds
# ---------------------------------------------------------------------------


class TestEnsureImageFirstCall:
    def test_builds_and_returns_tag(self, builder: ImageBuilder, client: docker.DockerClient):
        _remove_image(client)
        tag = builder.ensure_image()
        assert tag == IMAGE_TAG
        assert builder.image_exists() is True

    def test_image_has_hash_label(self, builder: ImageBuilder, client: docker.DockerClient):
        builder.ensure_image()
        image = client.images.get(IMAGE_TAG)
        assert _HASH_LABEL in image.labels
        assert len(image.labels[_HASH_LABEL]) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# ensure_image() — second call uses cache
# ---------------------------------------------------------------------------


class TestEnsureImageCached:
    def test_no_rebuild_on_second_call(self, builder: ImageBuilder, client: docker.DockerClient):
        builder.ensure_image()
        image_before = client.images.get(IMAGE_TAG)
        tag = builder.ensure_image()
        image_after = client.images.get(IMAGE_TAG)
        assert tag == IMAGE_TAG
        assert image_before.id == image_after.id


# ---------------------------------------------------------------------------
# build(force=True) rebuilds
# ---------------------------------------------------------------------------


class TestForceBuild:
    def test_force_rebuilds(self, builder: ImageBuilder, client: docker.DockerClient):
        builder.ensure_image()
        image_before = client.images.get(IMAGE_TAG)

        # Force a rebuild — the image ID may or may not change (content is the
        # same), but the call must succeed and return the tag.
        tag = builder.build(force=True)
        assert tag == IMAGE_TAG
        assert builder.image_exists() is True


# ---------------------------------------------------------------------------
# image_exists() — True after build
# ---------------------------------------------------------------------------


class TestImageExistsAfterBuild:
    def test_true_after_build(self, builder: ImageBuilder):
        builder.ensure_image()
        assert builder.image_exists() is True


# ---------------------------------------------------------------------------
# Built image can run Python
# ---------------------------------------------------------------------------


class TestRunBasicCommand:
    def test_python_arithmetic(self, builder: ImageBuilder, client: docker.DockerClient):
        builder.ensure_image()
        output = client.containers.run(
            IMAGE_TAG,
            ["python", "-c", "print(1+1)"],
            entrypoint="",
            remove=True,
        )
        assert output.strip() == b"2"
