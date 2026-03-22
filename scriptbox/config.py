"""Centralised configuration loaded from ``.env`` and ``os.environ``.

Precedence: **os.environ  >  .env file  >  defaults**.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when required configuration is missing."""


def _parse_env_file(path: str) -> dict[str, str]:
    """Parse a ``.env`` file into a dict.

    Skips blank lines and ``#`` comments.  Returns ``{}`` if the file
    is missing or unreadable.
    """
    try:
        text = Path(path).read_text()
    except Exception:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


@dataclass
class ScriptBoxConfig:
    bot_token: str
    chat_id: int
    scripts_dir: str = "./scripts"
    db_path: str = "./scriptbox.db"
    use_sandbox: bool = True
    daily_digest: bool = False
    daily_digest_time: str = "09:00"
    # Non-SCRIPTBOX_ keys from .env (API keys, etc.)
    secrets: dict[str, str] | None = None


def load_config(env_path: str = ".env") -> ScriptBoxConfig:
    """Load configuration from *env_path* with ``os.environ`` overrides.

    Raises :class:`ConfigError` if ``SCRIPTBOX_BOT_TOKEN`` or
    ``SCRIPTBOX_CHAT_ID`` are missing/empty.
    """
    file_vars = _parse_env_file(env_path)

    def _get(key: str, default: str | None = None) -> str | None:
        return os.environ.get(key) or file_vars.get(key) or default

    bot_token = _get("SCRIPTBOX_BOT_TOKEN")
    if not bot_token:
        raise ConfigError("SCRIPTBOX_BOT_TOKEN is required (set in .env or environment)")

    raw_chat_id = _get("SCRIPTBOX_CHAT_ID")
    if not raw_chat_id:
        raise ConfigError("SCRIPTBOX_CHAT_ID is required (set in .env or environment)")
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        raise ConfigError(f"SCRIPTBOX_CHAT_ID must be an integer, got {raw_chat_id!r}")

    scripts_dir = _get("SCRIPTBOX_SCRIPTS_DIR", "./scripts")
    db_path = _get("SCRIPTBOX_DB_PATH", "./scriptbox.db")
    raw_sandbox = _get("SCRIPTBOX_USE_SANDBOX", "true")
    use_sandbox = raw_sandbox.lower() in ("true", "1", "yes")

    raw_digest = _get("SCRIPTBOX_DAILY_DIGEST", "false")
    daily_digest = raw_digest.lower() in ("true", "1", "yes")
    daily_digest_time = _get("SCRIPTBOX_DAILY_DIGEST_TIME", "09:00")

    # Everything without the SCRIPTBOX_ prefix goes into secrets.
    secrets: dict[str, str] = {}
    for key, value in file_vars.items():
        if not key.startswith("SCRIPTBOX_"):
            secrets[key] = value
    # Also pick up non-SCRIPTBOX_ env vars that match .env keys.
    for key in secrets:
        env_val = os.environ.get(key)
        if env_val is not None:
            secrets[key] = env_val

    return ScriptBoxConfig(
        bot_token=bot_token,
        chat_id=chat_id,
        scripts_dir=scripts_dir,
        db_path=db_path,
        use_sandbox=use_sandbox,
        daily_digest=daily_digest,
        daily_digest_time=daily_digest_time,
        secrets=secrets,
    )
