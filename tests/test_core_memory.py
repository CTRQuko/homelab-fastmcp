"""Tests for core.memory adapter and backends."""
from __future__ import annotations

import pytest

from core.memory import MemoryBackend, load_backend
from core.memory.noop import NoopMemory
from core.memory.sqlite import SqliteMemory


def test_load_backend_noop():
    backend = load_backend("noop")
    assert isinstance(backend, NoopMemory)
    assert backend.name == "noop"


def test_load_backend_sqlite(tmp_path):
    backend = load_backend("sqlite", {"path": str(tmp_path / "m.db")})
    assert isinstance(backend, SqliteMemory)


def test_load_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown"):
        load_backend("engram")


def test_noop_returns_empty():
    b = NoopMemory()
    assert b.save("x") == ""
    assert b.search("x") == []
    assert b.get("abc") == {}
    b.update("abc", "y")
    b.delete("abc")


def test_sqlite_save_search_get(tmp_path):
    b = SqliteMemory(path=tmp_path / "m.db")
    entry_id = b.save("hello world", tags=["greeting"])
    assert entry_id
    results = b.search("hello")
    assert len(results) == 1
    assert results[0]["content"] == "hello world"
    fetched = b.get(entry_id)
    assert fetched["tags"] == ["greeting"]


def test_sqlite_update_and_delete(tmp_path):
    b = SqliteMemory(path=tmp_path / "m.db")
    entry_id = b.save("original")
    b.update(entry_id, "modified")
    assert b.get(entry_id)["content"] == "modified"
    b.delete(entry_id)
    assert b.get(entry_id) == {}


def test_sqlite_search_ordered_by_recency(tmp_path):
    b = SqliteMemory(path=tmp_path / "m.db")
    b.save("alpha entry")
    b.save("alpha again")
    results = b.search("alpha")
    # Most recent first
    assert results[0]["content"] == "alpha again"


def test_backend_is_abstract():
    with pytest.raises(TypeError):
        MemoryBackend()  # type: ignore[abstract]
