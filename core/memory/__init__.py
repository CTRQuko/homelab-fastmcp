"""Pluggable memory adapter.

The framework exposes a neutral :class:`MemoryBackend` interface so plugins
(and the LLM) can save, search and retrieve long-lived notes without caring
whether the implementation is Engram, a local SQLite store or a no-op stub.

Backend selection happens once, at router startup, via
:func:`load_backend`. Configuration is read from ``router.toml`` under the
``[memory]`` section.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryBackend(ABC):
    """Minimal surface every backend must implement."""

    name: str = "unknown"

    @abstractmethod
    def save(self, content: str, tags: list[str] | None = None, **kw: Any) -> str: ...

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get(self, id: str) -> dict[str, Any]: ...

    @abstractmethod
    def update(self, id: str, content: str) -> None: ...

    @abstractmethod
    def delete(self, id: str) -> None: ...


def load_backend(name: str, config: dict[str, Any] | None = None) -> MemoryBackend:
    """Factory that turns a config name into a live backend instance."""
    config = config or {}
    if name == "noop":
        from core.memory.noop import NoopMemory

        return NoopMemory()
    if name == "sqlite":
        from core.memory.sqlite import SqliteMemory

        return SqliteMemory(**config)
    raise ValueError(f"Unknown memory backend '{name}'. Available: noop, sqlite")
