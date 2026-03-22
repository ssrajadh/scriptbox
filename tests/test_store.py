from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scriptbox.store import ScriptStore


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Basic get / set
# ---------------------------------------------------------------------------


class TestGetSet:
    @pytest.mark.asyncio
    async def test_set_and_get_string(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        await store.set("greeting", "hello")
        assert await store.get("greeting") == "hello"

    @pytest.mark.asyncio
    async def test_set_and_get_complex_value(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        value = {"items": [1, 2, {"nested": True}], "count": 3}
        await store.set("data", value)
        assert await store.get("data") == value

    @pytest.mark.asyncio
    async def test_overwrite_existing_key(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        await store.set("k", "old")
        await store.set("k", "new")
        assert await store.get("k") == "new"


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


class TestDefaults:
    @pytest.mark.asyncio
    async def test_get_missing_key_returns_custom_default(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        assert await store.get("missing", default=42) == 42

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none_by_default(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        assert await store.get("missing") is None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_makes_key_missing(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        await store.set("k", "v")
        await store.delete("k")
        assert await store.get("k", default="gone") == "gone"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key_is_noop(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        await store.delete("nope")  # should not raise


# ---------------------------------------------------------------------------
# Namespace isolation
# ---------------------------------------------------------------------------


class TestNamespaceIsolation:
    @pytest.mark.asyncio
    async def test_different_namespaces_dont_see_each_other(self, db_path: str):
        store_a = ScriptStore(db_path, "script_a")
        store_b = ScriptStore(db_path, "script_b")
        await store_a.set("color", "red")
        await store_b.set("color", "blue")
        assert await store_a.get("color") == "red"
        assert await store_b.get("color") == "blue"

    @pytest.mark.asyncio
    async def test_keys_scoped_to_namespace(self, db_path: str):
        store_a = ScriptStore(db_path, "script_a")
        store_b = ScriptStore(db_path, "script_b")
        await store_a.set("x", 1)
        await store_a.set("y", 2)
        await store_b.set("z", 3)
        assert await store_a.keys() == ["x", "y"]
        assert await store_b.keys() == ["z"]


# ---------------------------------------------------------------------------
# keys()
# ---------------------------------------------------------------------------


class TestKeys:
    @pytest.mark.asyncio
    async def test_keys_empty_namespace(self, db_path: str):
        store = ScriptStore(db_path, "empty")
        assert await store.keys() == []

    @pytest.mark.asyncio
    async def test_keys_reflects_deletes(self, db_path: str):
        store = ScriptStore(db_path, "ns1")
        await store.set("a", 1)
        await store.set("b", 2)
        await store.delete("a")
        assert await store.keys() == ["b"]


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_writes_different_namespaces(self, db_path: str):
        store_a = ScriptStore(db_path, "ns_a")
        store_b = ScriptStore(db_path, "ns_b")

        async def write_many(store: ScriptStore, prefix: str):
            for i in range(50):
                await store.set(f"{prefix}_{i}", i)

        await asyncio.gather(
            write_many(store_a, "a"),
            write_many(store_b, "b"),
        )

        assert len(await store_a.keys()) == 50
        assert len(await store_b.keys()) == 50
        assert await store_a.get("a_49") == 49
        assert await store_b.get("b_0") == 0
