from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_MEMORY_RE = re.compile(r"^\d+[bkmg]$", re.IGNORECASE)
_VALID_NETWORKS = ("bridge", "none", "restricted")


class SandboxConfigError(Exception):
    """Raised when sandbox configuration values are invalid."""


@dataclass
class SandboxConfig:
    enabled: bool = True
    memory: str = "256m"
    cpu: float = 1.0
    timeout: int = 30
    network: str = "bridge"
    allowed_domains: list[str] = field(default_factory=list)
    read_only_root: bool = True
    pids_limit: int = 64
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_meta(cls, meta: dict[str, Any]) -> SandboxConfig:
        """Build a config from a script's META dict.

        If *meta* has no ``"sandbox"`` key the defaults are returned.
        Otherwise the sandbox dict is overlaid on top of the defaults
        and validated.
        """
        sandbox = meta.get("sandbox")
        if not sandbox:
            return cls()
        if not isinstance(sandbox, dict):
            raise SandboxConfigError(
                f"META['sandbox'] must be a dict, got {type(sandbox).__name__}"
            )
        cfg = cls(
            enabled=sandbox.get("enabled", cls.enabled),
            memory=sandbox.get("memory", cls.memory),
            cpu=sandbox.get("cpu", cls.cpu),
            timeout=sandbox.get("timeout", cls.timeout),
            network=sandbox.get("network", cls.network),
            allowed_domains=sandbox.get("allowed_domains", list()),
            read_only_root=sandbox.get("read_only_root", cls.read_only_root),
            pids_limit=sandbox.get("pids_limit", cls.pids_limit),
            env=sandbox.get("env", dict()),
        )
        cfg._validate()
        return cfg

    @classmethod
    def disabled(cls) -> SandboxConfig:
        """Return a config with sandboxing turned off."""
        return cls(enabled=False)

    def _validate(self) -> None:
        if not _MEMORY_RE.match(self.memory):
            raise SandboxConfigError(
                f"Invalid memory format {self.memory!r} — "
                "expected pattern like '128m' or '1g'"
            )
        if self.cpu <= 0:
            raise SandboxConfigError(
                f"cpu must be > 0, got {self.cpu}"
            )
        if self.timeout <= 0:
            raise SandboxConfigError(
                f"timeout must be > 0, got {self.timeout}"
            )
        if self.network not in _VALID_NETWORKS:
            raise SandboxConfigError(
                f"network must be one of {_VALID_NETWORKS}, got {self.network!r}"
            )
        if self.pids_limit <= 0:
            raise SandboxConfigError(
                f"pids_limit must be > 0, got {self.pids_limit}"
            )
