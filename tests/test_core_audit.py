"""Tests for core.audit fire-and-forget logger."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from core import audit
from core.audit import log_tool_call


def test_log_writes_json_line(tmp_path):
    path = tmp_path / "audit.log"
    log_tool_call("p", "tool_x", {"a": 1}, 12.3, "ok", path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["plugin"] == "p"
    assert entry["tool"] == "tool_x"
    assert entry["status"] == "ok"
    assert entry["duration_ms"] == 12.3
    assert len(entry["args_hash"]) == 16


def test_log_never_raises_on_bad_args(tmp_path):
    path = tmp_path / "audit.log"

    class Weird:
        def __repr__(self):
            return "weird"

    log_tool_call("p", "t", {"x": Weird()}, 0.0, "ok", path=path)
    assert path.exists()


def test_log_appends(tmp_path):
    path = tmp_path / "audit.log"
    log_tool_call("p", "a", {}, 1, "ok", path=path)
    log_tool_call("p", "b", {}, 2, "ok", path=path)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

def test_rotation_triggers_when_log_exceeds_max_size(tmp_path, monkeypatch):
    """Cuando el log activo supera ``_MAX_LOG_SIZE``, la siguiente escritura
    rota el archivo a ``.1`` y empieza un log fresco."""
    monkeypatch.setattr(audit, "_MAX_LOG_SIZE", 100)
    path = tmp_path / "audit.log"
    # Pre-llenar a >100 bytes para forzar rotación en la siguiente escritura.
    path.write_text("x" * 200, encoding="utf-8")
    assert path.stat().st_size > 100

    log_tool_call("p", "after_rotation", {}, 0.1, "ok", path=path)

    # El log antiguo debe estar como .1, el nuevo activo solo tiene 1 línea.
    assert path.with_name("audit.log.1").exists()
    assert path.with_name("audit.log.1").read_text(encoding="utf-8") == "x" * 200
    new_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(new_lines) == 1
    assert json.loads(new_lines[0])["tool"] == "after_rotation"


def test_rotation_respects_backup_count(tmp_path, monkeypatch):
    """Cuando todos los slots de backup están llenos, el más viejo se borra
    al rotar. Slots intermedios se desplazan hacia fuera (.6→.7, …, .1→.2)."""
    monkeypatch.setattr(audit, "_MAX_LOG_SIZE", 100)
    monkeypatch.setattr(audit, "_BACKUP_COUNT", 3)
    path = tmp_path / "audit.log"
    # Estado inicial: log activo lleno + 3 backups con contenido único.
    path.write_text("active" * 30, encoding="utf-8")  # >100 bytes
    path.with_name("audit.log.1").write_text("backup1", encoding="utf-8")
    path.with_name("audit.log.2").write_text("backup2", encoding="utf-8")
    path.with_name("audit.log.3").write_text("backup3", encoding="utf-8")

    log_tool_call("p", "trigger", {}, 0.0, "ok", path=path)

    # Slot .3 ahora contiene lo que estaba en .2 (.3 viejo se borró).
    # Slot .2 contiene lo que estaba en .1.
    # Slot .1 contiene el log activo previo.
    assert path.with_name("audit.log.3").read_text(encoding="utf-8") == "backup2"
    assert path.with_name("audit.log.2").read_text(encoding="utf-8") == "backup1"
    assert path.with_name("audit.log.1").read_text(encoding="utf-8") == "active" * 30
    # Slot .4 nunca debe crearse — el cap es _BACKUP_COUNT=3.
    assert not path.with_name("audit.log.4").exists()
    # Log activo es nuevo y solo tiene la entrada que disparó la rotación.
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_rotation_does_not_trigger_below_threshold(tmp_path, monkeypatch):
    """Confirma el lado feliz: si el log NO supera el umbral, no se rota."""
    monkeypatch.setattr(audit, "_MAX_LOG_SIZE", 10_000)
    path = tmp_path / "audit.log"
    log_tool_call("p", "first", {}, 0.0, "ok", path=path)
    log_tool_call("p", "second", {}, 0.0, "ok", path=path)
    # 2 líneas en el activo, ningún backup.
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert not path.with_name("audit.log.1").exists()


# ---------------------------------------------------------------------------
# _resolve_default_audit_path
# ---------------------------------------------------------------------------

def test_resolve_default_audit_path_explicit_env(monkeypatch, tmp_path):
    """``MIMIR_AUDIT_LOG`` define el path canónico cuando está seteado."""
    target = tmp_path / "custom.log"
    monkeypatch.setenv("MIMIR_AUDIT_LOG", str(target))
    monkeypatch.delenv("HOMELAB_FASTMCP_AUDIT_LOG", raising=False)
    assert audit._resolve_default_audit_path() == Path(str(target))


def test_resolve_default_audit_path_legacy_env_warns(monkeypatch, tmp_path):
    """``HOMELAB_FASTMCP_AUDIT_LOG`` (legacy) debe emitir DeprecationWarning
    pero seguir funcionando como fallback hasta que se elimine."""
    target = tmp_path / "legacy.log"
    monkeypatch.delenv("MIMIR_AUDIT_LOG", raising=False)
    monkeypatch.setenv("HOMELAB_FASTMCP_AUDIT_LOG", str(target))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = audit._resolve_default_audit_path()
    assert result == Path(str(target))
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert any("HOMELAB_FASTMCP_AUDIT_LOG" in str(w.message) for w in caught)


def test_resolve_default_audit_path_falls_back_to_framework_root(monkeypatch):
    """Sin env vars, cae a ``<framework_root>/config/audit.log``."""
    monkeypatch.delenv("MIMIR_AUDIT_LOG", raising=False)
    monkeypatch.delenv("HOMELAB_FASTMCP_AUDIT_LOG", raising=False)
    result = audit._resolve_default_audit_path()
    # El path acaba en config/audit.log y la abuela del módulo es el repo.
    assert result.name == "audit.log"
    assert result.parent.name == "config"
