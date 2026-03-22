from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from scriptbox.store import ScriptStore

logger = logging.getLogger(__name__)


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
        secrets_path: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.script_id = script_id
        self.store = ScriptStore(db_path, namespace=script_id)
        self.inputs: dict[str, Any] = inputs if inputs is not None else {}
        self.http = http_client if http_client is not None else httpx.AsyncClient()
        self.llm = StubLLMClient()
        self.telegram = StubTelegramClient()
        self.secrets: dict[str, Any] = self._load_secrets(secrets_path)

    @staticmethod
    def _load_secrets(path: str | None) -> dict[str, Any]:
        if path is None:
            return {}
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            logger.warning("Failed to load secrets from %s", path, exc_info=True)
            return {}
