from __future__ import annotations

import json
from typing import Any

import aiosqlite


class ScriptStore:
    """Per-script key-value store backed by SQLite."""

    def __init__(self, db_path: str, namespace: str) -> None:
        self._db_path = db_path
        self._namespace = namespace
        self._table_ready = False

    async def _ensure_table(self, db: aiosqlite.Connection) -> None:
        if not self._table_ready:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS store "
                "(namespace TEXT, key TEXT, value TEXT, "
                "PRIMARY KEY(namespace, key))"
            )
            await db.commit()
            self._table_ready = True

    async def get(self, key: str, default: Any = None) -> Any:
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_table(db)
            cursor = await db.execute(
                "SELECT value FROM store WHERE namespace = ? AND key = ?",
                (self._namespace, key),
            )
            row = await cursor.fetchone()
            if row is None:
                return default
            return json.loads(row[0])

    async def set(self, key: str, value: Any) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_table(db)
            await db.execute(
                "INSERT INTO store (namespace, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value",
                (self._namespace, key, json.dumps(value)),
            )
            await db.commit()

    async def delete(self, key: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_table(db)
            await db.execute(
                "DELETE FROM store WHERE namespace = ? AND key = ?",
                (self._namespace, key),
            )
            await db.commit()

    async def keys(self) -> list[str]:
        async with aiosqlite.connect(self._db_path) as db:
            await self._ensure_table(db)
            cursor = await db.execute(
                "SELECT key FROM store WHERE namespace = ? ORDER BY key",
                (self._namespace,),
            )
            rows = await cursor.fetchall()
            return [r[0] for r in rows]
