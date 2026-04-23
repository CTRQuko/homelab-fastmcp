"""Local SQLite memory backend — offline fallback with no external deps.

Schema is intentionally minimal: one ``entries`` table with ``id`` (uuid4),
``content`` and JSON-serialised ``tags``. Search is a ``LIKE`` scan over
content plus a tag filter. Good enough for the MVP until a real backend
(Engram, claude_mem) takes over.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.memory import MemoryBackend

_FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent.parent


class SqliteMemory(MemoryBackend):
    name = "sqlite"

    def __init__(self, path: str | Path = "config/memory.db") -> None:
        p = Path(path)
        if not p.is_absolute():
            # Relative paths resolve against the framework root, not CWD,
            # so the router stays deterministic no matter where it was
            # launched from.
            p = _FRAMEWORK_ROOT / p
        self._path = p
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, content: str, tags: list[str] | None = None, **kw: Any) -> str:
        entry_id = str(uuid.uuid4())
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO entries (id, content, tags, created_at) VALUES (?, ?, ?, ?)",
                (entry_id, content, json.dumps(tags or []), time.time()),
            )
        return entry_id

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        like = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, tags, created_at FROM entries "
                "WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (like, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "content": r["content"],
                "tags": json.loads(r["tags"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get(self, id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, content, tags, created_at FROM entries WHERE id = ?",
                (id,),
            ).fetchone()
        if not row:
            return {}
        return {
            "id": row["id"],
            "content": row["content"],
            "tags": json.loads(row["tags"]),
            "created_at": row["created_at"],
        }

    def update(self, id: str, content: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE entries SET content = ? WHERE id = ?", (content, id))

    def delete(self, id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM entries WHERE id = ?", (id,))
