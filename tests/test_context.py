from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from scriptbox.context import ScriptContext, StubLLMClient, StubTelegramClient
from scriptbox.store import ScriptStore


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "ctx_test.db")


@pytest.fixture()
def env_file(tmp_path: Path) -> str:
    p = tmp_path / ".env"
    p.write_text("API_KEY=test123\nOTHER=hello\n")
    return str(p)


# ---------------------------------------------------------------------------
# Attribute types
# ---------------------------------------------------------------------------


class TestAttributes:
    def test_script_id(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert ctx.script_id == "my_script"

    def test_store_type(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert isinstance(ctx.store, ScriptStore)

    def test_inputs_type(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert isinstance(ctx.inputs, dict)

    def test_http_type(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert isinstance(ctx.http, httpx.AsyncClient)

    def test_llm_type(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert isinstance(ctx.llm, StubLLMClient)

    def test_telegram_type(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert isinstance(ctx.telegram, StubTelegramClient)

    def test_secrets_type(self, db_path: str):
        ctx = ScriptContext("my_script", db_path)
        assert isinstance(ctx.secrets, dict)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_inputs_defaults_to_empty_dict(self, db_path: str):
        ctx = ScriptContext("s", db_path)
        assert ctx.inputs == {}

    def test_secrets_empty_when_no_file(self, db_path: str):
        ctx = ScriptContext("s", db_path, env_path=None)
        assert ctx.secrets == {}


# ---------------------------------------------------------------------------
# Secrets (.env loading)
# ---------------------------------------------------------------------------


class TestSecrets:
    def test_loads_from_env_file(self, db_path: str, env_file: str):
        ctx = ScriptContext("s", db_path, env_path=env_file)
        assert ctx.secrets == {"API_KEY": "test123", "OTHER": "hello"}

    def test_bad_path_gives_empty_dict(self, db_path: str):
        ctx = ScriptContext("s", db_path, env_path="/no/such/.env")
        assert ctx.secrets == {}

    def test_comments_and_blanks_skipped(self, db_path: str, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text("# comment\n\nKEY=val\n  \n# another\n")
        ctx = ScriptContext("s", db_path, env_path=str(p))
        assert ctx.secrets == {"KEY": "val"}

    def test_quoted_values_stripped(self, db_path: str, tmp_path: Path):
        p = tmp_path / ".env"
        p.write_text('A="double"\nB=\'single\'\nC=plain\n')
        ctx = ScriptContext("s", db_path, env_path=str(p))
        assert ctx.secrets == {"A": "double", "B": "single", "C": "plain"}


# ---------------------------------------------------------------------------
# Store scoping
# ---------------------------------------------------------------------------


class TestStoreScoping:
    @pytest.mark.asyncio
    async def test_two_contexts_have_independent_stores(self, db_path: str):
        ctx_a = ScriptContext("script_a", db_path)
        ctx_b = ScriptContext("script_b", db_path)
        await ctx_a.store.set("key", "value_a")
        await ctx_b.store.set("key", "value_b")
        assert await ctx_a.store.get("key") == "value_a"
        assert await ctx_b.store.get("key") == "value_b"


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------


class TestStubs:
    @pytest.mark.asyncio
    async def test_llm_complete_returns_stub(self, db_path: str):
        ctx = ScriptContext("s", db_path)
        result = await ctx.llm.complete("hello")
        assert result == "[LLM stub response]"

    @pytest.mark.asyncio
    async def test_llm_complete_accepts_model(self, db_path: str):
        ctx = ScriptContext("s", db_path)
        result = await ctx.llm.complete("hello", model="gpt-4")
        assert result == "[LLM stub response]"

    @pytest.mark.asyncio
    async def test_telegram_send_does_not_raise(self, db_path: str):
        ctx = ScriptContext("s", db_path)
        await ctx.telegram.send("test message")

    @pytest.mark.asyncio
    async def test_telegram_ask_returns_true(self, db_path: str):
        ctx = ScriptContext("s", db_path)
        assert await ctx.telegram.ask("proceed?") is True


# ---------------------------------------------------------------------------
# Custom http client
# ---------------------------------------------------------------------------


class TestHttpClient:
    def test_accepts_custom_client(self, db_path: str):
        client = httpx.AsyncClient(base_url="https://example.com")
        ctx = ScriptContext("s", db_path, http_client=client)
        assert ctx.http is client
