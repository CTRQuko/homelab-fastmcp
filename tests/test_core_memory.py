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


def test_sqlite_relative_path_resolves_against_framework_root(tmp_path, monkeypatch):
    """Deuda técnica: paths relativos deben resolverse contra el root del framework,
    no contra el CWD, para que el router sea determinista sin importar desde dónde
    se lance."""
    from core.memory import sqlite as sqlite_mod

    # Simulamos que el framework vive en tmp_path, y lanzamos desde otro dir.
    fake_root = tmp_path / "framework"
    fake_root.mkdir()
    monkeypatch.setattr(sqlite_mod, "_FRAMEWORK_ROOT", fake_root)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)

    b = SqliteMemory(path="config/mem.db")
    # El fichero DB debe crearse bajo fake_root, no bajo other/.
    expected = fake_root / "config" / "mem.db"
    assert expected.exists()
    assert not (other / "config" / "mem.db").exists()


def test_sqlite_absolute_path_unchanged(tmp_path):
    abs_path = tmp_path / "abs.db"
    b = SqliteMemory(path=abs_path)
    b.save("x")
    assert abs_path.exists()


def test_find_framework_root_locates_by_markers(tmp_path, monkeypatch):
    """``_find_framework_root`` sube buscando router.py + pyproject.toml
    como marcadores. Reemplaza el ``parent.parent.parent`` fragil que
    rompia si movian ``core/`` de sitio (R6)."""
    from core.memory import sqlite as sqlite_mod

    fake_root = tmp_path / "fw"
    deep = fake_root / "core" / "memory"
    deep.mkdir(parents=True)
    (fake_root / "router.py").write_text("# marker", encoding="utf-8")
    (fake_root / "pyproject.toml").write_text("[tool]\n", encoding="utf-8")
    fake_module_file = deep / "sqlite.py"
    fake_module_file.write_text("# marker", encoding="utf-8")

    monkeypatch.setattr(sqlite_mod, "__file__", str(fake_module_file))
    assert sqlite_mod._find_framework_root() == fake_root


def test_find_framework_root_raises_when_markers_missing(tmp_path, monkeypatch):
    from core.memory import sqlite as sqlite_mod

    orphan = tmp_path / "no-root" / "deep" / "sqlite.py"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("# marker", encoding="utf-8")

    monkeypatch.setattr(sqlite_mod, "__file__", str(orphan))
    # Nested inside tmp_path which has no router.py/pyproject.toml markers
    # and neither does any ancestor within reach (Windows/POSIX-safe: the
    # walk stops at the filesystem root, which also lacks those markers).
    import pytest

    with pytest.raises(RuntimeError, match="could not locate framework root"):
        sqlite_mod._find_framework_root()
