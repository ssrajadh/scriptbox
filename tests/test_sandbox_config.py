from __future__ import annotations

import pytest

from scriptbox.sandbox.config import SandboxConfig, SandboxConfigError


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_enabled(self):
        cfg = SandboxConfig()
        assert cfg.enabled is True

    def test_default_memory(self):
        assert SandboxConfig().memory == "256m"

    def test_default_cpu(self):
        assert SandboxConfig().cpu == 1.0

    def test_default_timeout(self):
        assert SandboxConfig().timeout == 30

    def test_default_network(self):
        assert SandboxConfig().network == "bridge"

    def test_default_allowed_domains(self):
        assert SandboxConfig().allowed_domains == []

    def test_default_read_only_root(self):
        assert SandboxConfig().read_only_root is True

    def test_default_env(self):
        assert SandboxConfig().env == {}


# ---------------------------------------------------------------------------
# from_meta — empty / missing sandbox
# ---------------------------------------------------------------------------


class TestFromMetaDefaults:
    def test_no_sandbox_key(self):
        cfg = SandboxConfig.from_meta({"name": "test"})
        assert cfg == SandboxConfig()

    def test_sandbox_none(self):
        cfg = SandboxConfig.from_meta({"name": "test", "sandbox": None})
        assert cfg == SandboxConfig()

    def test_sandbox_empty_dict(self):
        cfg = SandboxConfig.from_meta({"name": "test", "sandbox": {}})
        assert cfg == SandboxConfig()


# ---------------------------------------------------------------------------
# from_meta — partial overlay
# ---------------------------------------------------------------------------


class TestFromMetaPartial:
    def test_only_memory(self):
        cfg = SandboxConfig.from_meta({"sandbox": {"memory": "512m"}})
        assert cfg.memory == "512m"
        assert cfg.cpu == 1.0  # default preserved
        assert cfg.timeout == 30

    def test_only_network(self):
        cfg = SandboxConfig.from_meta({"sandbox": {"network": "none"}})
        assert cfg.network == "none"
        assert cfg.memory == "256m"

    def test_only_timeout(self):
        cfg = SandboxConfig.from_meta({"sandbox": {"timeout": 60}})
        assert cfg.timeout == 60

    def test_only_cpu(self):
        cfg = SandboxConfig.from_meta({"sandbox": {"cpu": 0.5}})
        assert cfg.cpu == 0.5


# ---------------------------------------------------------------------------
# from_meta — full sandbox dict
# ---------------------------------------------------------------------------


class TestFromMetaFull:
    def test_all_fields(self):
        cfg = SandboxConfig.from_meta(
            {
                "sandbox": {
                    "enabled": False,
                    "memory": "1g",
                    "cpu": 2.0,
                    "timeout": 120,
                    "network": "restricted",
                    "allowed_domains": ["api.example.com"],
                    "read_only_root": False,
                    "env": {"DEBUG": "1"},
                }
            }
        )
        assert cfg.enabled is False
        assert cfg.memory == "1g"
        assert cfg.cpu == 2.0
        assert cfg.timeout == 120
        assert cfg.network == "restricted"
        assert cfg.allowed_domains == ["api.example.com"]
        assert cfg.read_only_root is False
        assert cfg.env == {"DEBUG": "1"}


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_memory_format(self):
        with pytest.raises(SandboxConfigError, match="memory"):
            SandboxConfig.from_meta({"sandbox": {"memory": "256mb"}})

    def test_invalid_memory_no_unit(self):
        with pytest.raises(SandboxConfigError, match="memory"):
            SandboxConfig.from_meta({"sandbox": {"memory": "256"}})

    def test_invalid_network(self):
        with pytest.raises(SandboxConfigError, match="network"):
            SandboxConfig.from_meta({"sandbox": {"network": "host"}})

    def test_cpu_zero(self):
        with pytest.raises(SandboxConfigError, match="cpu"):
            SandboxConfig.from_meta({"sandbox": {"cpu": 0}})

    def test_cpu_negative(self):
        with pytest.raises(SandboxConfigError, match="cpu"):
            SandboxConfig.from_meta({"sandbox": {"cpu": -1}})

    def test_timeout_zero(self):
        with pytest.raises(SandboxConfigError, match="timeout"):
            SandboxConfig.from_meta({"sandbox": {"timeout": 0}})

    def test_timeout_negative(self):
        with pytest.raises(SandboxConfigError, match="timeout"):
            SandboxConfig.from_meta({"sandbox": {"timeout": -5}})

    def test_sandbox_not_dict(self):
        with pytest.raises(SandboxConfigError, match="dict"):
            SandboxConfig.from_meta({"sandbox": "yes"})


# ---------------------------------------------------------------------------
# disabled()
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_enabled_false(self):
        cfg = SandboxConfig.disabled()
        assert cfg.enabled is False

    def test_other_fields_are_defaults(self):
        cfg = SandboxConfig.disabled()
        assert cfg.memory == "256m"
        assert cfg.cpu == 1.0
        assert cfg.timeout == 30
