"""Telegram notification clients.

- :class:`TelegramNotifier` — sends messages via the Bot API (httpx).
- :class:`SandboxNotifier` — buffers messages for later dispatch by the
  Docker executor (used inside containers where network may be restricted).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from scriptbox.executor import ExecutionResult
from scriptbox.telegram.formatter import MAX_MESSAGE_LEN, format_execution_results

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramNotifier:
    """Sends messages to a single Telegram chat via the Bot API."""

    def __init__(self, bot_token: str, chat_id: int) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._base = _API_BASE.format(token=bot_token)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, message: str, parse_mode: str = "HTML") -> None:
        """Send *message* to the configured chat.

        Messages longer than 4096 characters are split across multiple
        sends.  Failures are logged but never raised — a broken
        Telegram link must not break script execution.
        """
        chunks = _split_message(message)
        for chunk in chunks:
            await self._post_send(chunk, parse_mode)

    async def send_results(
        self, results: list[ExecutionResult], script_id: str
    ) -> None:
        """Format and send execution results."""
        text = f"<b>Results for {script_id}</b>\n{format_execution_results(results)}"
        await self.send(text)

    async def ask(self, question: str, timeout: int = 300) -> bool:
        """Send *question* with Yes/No inline buttons and wait for a reply.

        Uses ``getUpdates`` polling to retrieve the callback query
        response — no PTB Application required.  Returns ``True`` for
        "yes", ``False`` for "no" or on timeout.
        """
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Yes", "callback_data": "yes"},
                    {"text": "❌ No", "callback_data": "no"},
                ]
            ]
        }
        msg = await self._post_send(question, "HTML", reply_markup=keyboard)
        if msg is None:
            return False

        return await self._poll_callback(timeout)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _post_send(
        self,
        text: str,
        parse_mode: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """POST to ``sendMessage`` and return the result dict, or None."""
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base}/sendMessage",
                    data=payload,
                    timeout=15,
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.error(
                        "Telegram sendMessage failed: %s",
                        data.get("description", data),
                    )
                    return None
                return data.get("result")
        except Exception:
            logger.error("Telegram send failed", exc_info=True)
            return None

    async def _poll_callback(self, timeout: int) -> bool:
        """Poll ``getUpdates`` for a callback_query matching our chat."""
        deadline = asyncio.get_event_loop().time() + timeout
        offset: int | None = None

        try:
            async with httpx.AsyncClient() as client:
                while asyncio.get_event_loop().time() < deadline:
                    remaining = max(1, int(deadline - asyncio.get_event_loop().time()))
                    poll_timeout = min(remaining, 30)

                    params: dict[str, Any] = {
                        "timeout": poll_timeout,
                        "allowed_updates": json.dumps(["callback_query"]),
                    }
                    if offset is not None:
                        params["offset"] = offset

                    resp = await client.get(
                        f"{self._base}/getUpdates",
                        params=params,
                        timeout=poll_timeout + 10,
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        await asyncio.sleep(1)
                        continue

                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        cb = update.get("callback_query")
                        if cb and cb.get("message", {}).get("chat", {}).get("id") == self._chat_id:
                            answer = cb.get("data", "no")
                            # Acknowledge the callback
                            await client.post(
                                f"{self._base}/answerCallbackQuery",
                                data={"callback_query_id": cb["id"]},
                                timeout=5,
                            )
                            return answer == "yes"
        except Exception:
            logger.error("Telegram ask polling failed", exc_info=True)

        return False


# ======================================================================
# SandboxNotifier — used inside Docker containers
# ======================================================================


class SandboxNotifier:
    """Buffers messages instead of sending them.

    The container harness writes the buffer to
    ``/run/notifications.json`` on exit; the :class:`DockerExecutor`
    then dispatches them through a real :class:`TelegramNotifier`.
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send(self, message: str, parse_mode: str = "HTML") -> None:
        self.messages.append({"text": message, "parse_mode": parse_mode})

    async def ask(self, question: str, timeout: int = 300) -> bool:
        raise NotImplementedError(
            "ask() is not supported inside a sandbox container. "
            "Use ctx.telegram.send() to queue notifications instead."
        )

    def to_json(self) -> str:
        """Serialize buffered messages to a JSON string."""
        return json.dumps(self.messages)

    @staticmethod
    def from_json(data: str) -> list[dict[str, str]]:
        """Deserialize a notifications JSON payload."""
        return json.loads(data)


# ======================================================================
# Helpers
# ======================================================================


def _split_message(text: str) -> list[str]:
    """Split *text* into chunks of at most ``MAX_MESSAGE_LEN`` chars."""
    if len(text) <= MAX_MESSAGE_LEN:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= MAX_MESSAGE_LEN:
            chunks.append(text)
            break
        # Try to split at a newline near the limit.
        cut = text.rfind("\n", 0, MAX_MESSAGE_LEN)
        if cut <= 0:
            cut = MAX_MESSAGE_LEN
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
