"""No-op memory backend — discards writes, returns empty reads.

Useful as a default when the user has not configured any persistent store
and to keep tests deterministic without touching disk.
"""
from __future__ import annotations

from typing import Any

from core.memory import MemoryBackend


class NoopMemory(MemoryBackend):
    name = "noop"

    def save(self, content: str, tags: list[str] | None = None, **kw: Any) -> str:
        return ""

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return []

    def get(self, id: str) -> dict[str, Any]:
        return {}

    def update(self, id: str, content: str) -> None:
        return None

    def delete(self, id: str) -> None:
        return None
