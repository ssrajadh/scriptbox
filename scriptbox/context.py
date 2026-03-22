from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from scriptbox.store import ScriptStore

logger = logging.getLogger(__name__)

_DEFAULT_ENV_PATH = ".env"


class StubLLMClient:
    """Placeholder LLM client — will be replaced with a real one in Phase 4."""

    async def complete(self, prompt: str, model: str = "stub") -> str:
        return "[LLM stub response]"


class StubTelegramClient:
    """Placeholder Telegram client — will be replaced in Phase 3."""

    async def send(self, message: str) -> None:
        logger.info("StubTelegram: %s", message)

    async def ask(self, question: str) -> bool:
        logger.info("StubTelegram ask: %s", question)
        return True


class ScriptContext:
    """Runtime context passed to every script's ``run(ctx)`` function."""

    def __init__(
        self,
        script_id: str,
        db_path: str,
        inputs: dict[str, Any] | None = None,
        env_path: str | None = _DEFAULT_ENV_PATH,
        http_client: httpx.AsyncClient | None = None,
        telegram: Any | None = None,
    ) -> None:
        self.script_id = script_id
        self.store = ScriptStore(db_path, namespace=script_id)
        self.inputs: dict[str, Any] = inputs if inputs is not None else {}
        self.http = http_client if http_client is not None else httpx.AsyncClient()
        self.llm = StubLLMClient()
        self.telegram = telegram if telegram is not None else StubTelegramClient()
        self.secrets: dict[str, str] = self._load_env(env_path)

    @staticmethod
    def _load_env(path: str | None) -> dict[str, str]:
        """Parse a ``.env`` file into a dict.

        Skips blank lines and ``#`` comments.  Returns ``{}`` if the
        file is missing or unreadable.
        """
        if path is None:
            return {}
        try:
            text = Path(path).read_text()
        except Exception:
            return {}
        secrets: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes if present.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key:
                secrets[key] = value
        return secrets
