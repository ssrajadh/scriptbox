from scriptbox.sandbox.config import SandboxConfig, SandboxConfigError

# ImageBuilder and DockerExecutor depend on the `docker` SDK which is only
# available on the host, not inside runner containers.  Lazy-import them to
# avoid ImportError when the harness runs inside Docker.


def __getattr__(name: str):
    if name == "ImageBuilder":
        from scriptbox.sandbox.image_builder import ImageBuilder

        return ImageBuilder
    if name == "DockerExecutor":
        from scriptbox.sandbox.docker_executor import DockerExecutor

        return DockerExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["SandboxConfig", "SandboxConfigError", "DockerExecutor", "ImageBuilder"]
