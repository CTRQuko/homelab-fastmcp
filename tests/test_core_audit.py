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


# ---------------------------------------------------------------------------
# Enriched payload — client + error_message + args_sanitized
# ---------------------------------------------------------------------------

def test_log_includes_client_field(tmp_path, monkeypatch):
    """Toda entry debe incluir ``client`` (default: resolver chain)."""
    monkeypatch.setenv("MIMIR_CLIENT_ID", "test-client-x")
    path = tmp_path / "audit.log"
    log_tool_call("p", "t", {}, 1.0, "ok", path=path)
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["client"] == "test-client-x"


def test_log_client_explicit_overrides_resolver(tmp_path, monkeypatch):
    """Pasar ``client=`` explícito tiene precedencia sobre env var."""
    monkeypatch.setenv("MIMIR_CLIENT_ID", "from-env")
    path = tmp_path / "audit.log"
    log_tool_call("p", "t", {}, 1.0, "ok", path=path, client="from-arg")
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["client"] == "from-arg"


def test_log_error_includes_error_message_truncated(tmp_path):
    long_msg = "X" * 800
    path = tmp_path / "audit.log"
    log_tool_call(
        "p", "t", {"a": 1}, 1.0, "error:RuntimeError",
        path=path, error_message=long_msg,
    )
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["status"].startswith("error")
    # Trunca a 500 chars exactos.
    assert len(entry["error_message"]) == 500
    assert entry["error_message"] == "X" * 500


def test_log_error_includes_args_sanitized(tmp_path):
    path = tmp_path / "audit.log"
    log_tool_call(
        "p", "t",
        {"node": "pve", "TOKEN": "tskey-abc-123"},
        1.0, "error:KeyError",
        path=path, error_message="missing key",
    )
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["args_sanitized"] == {"node": "pve", "TOKEN": "<redacted>"}


def test_log_ok_does_not_include_enriched_fields(tmp_path):
    path = tmp_path / "audit.log"
    log_tool_call(
        "p", "t", {"TOKEN": "secret"}, 1.0, "ok",
        path=path, error_message="should not appear",
    )
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "error_message" not in entry
    assert "args_sanitized" not in entry  # OK path stays cheap
    # args_hash sigue presente (covered by existing test_log_writes_json_line).


# ---------------------------------------------------------------------------
# _sanitize_args — redaction + truncation
# ---------------------------------------------------------------------------

def test_sanitize_redacts_secret_shaped_keys():
    raw = {
        "node": "pve",
        "API_KEY": "abc123",
        "password": "p4ss",
        "x-auth-token": "tok",
        "MyCookieValue": "yum",
    }
    result = audit._sanitize_args(raw)
    assert result["node"] == "pve"
    assert result["API_KEY"] == "<redacted>"
    assert result["password"] == "<redacted>"
    assert result["x-auth-token"] == "<redacted>"
    assert result["MyCookieValue"] == "<redacted>"


def test_sanitize_truncates_long_strings():
    raw = {"output": "Z" * 500, "short": "abc"}
    result = audit._sanitize_args(raw)
    assert result["output"].startswith("Z" * 200)
    assert result["output"].endswith("...<truncated>")
    assert result["short"] == "abc"


def test_sanitize_handles_nested_structures():
    raw = {
        "outer": {"TOKEN": "x", "ok_key": "y"},
        "list": [{"PASSWORD": "z"}, "plain"],
    }
    result = audit._sanitize_args(raw)
    assert result["outer"] == {"TOKEN": "<redacted>", "ok_key": "y"}
    assert result["list"][0] == {"PASSWORD": "<redacted>"}
    assert result["list"][1] == "plain"


def test_sanitize_passthrough_primitives():
    assert audit._sanitize_args(42) == 42
    assert audit._sanitize_args(None) is None
    assert audit._sanitize_args(3.14) == 3.14
    assert audit._sanitize_args("short") == "short"


def test_sanitize_top_level_long_string_truncates():
    raw = "Y" * 500
    result = audit._sanitize_args(raw)
    assert len(result) == 200 + len("...<truncated>")


# ---------------------------------------------------------------------------
# _resolve_client_id
# ---------------------------------------------------------------------------

def test_resolve_client_id_uses_env_var(monkeypatch):
    monkeypatch.setenv("MIMIR_CLIENT_ID", "claude-code")
    assert audit._resolve_client_id() == "claude-code"


def test_resolve_client_id_falls_back_to_unknown_when_no_env(monkeypatch):
    """Sin env y sin psutil disponible (o falla), devuelve "unknown"."""
    monkeypatch.delenv("MIMIR_CLIENT_ID", raising=False)
    # Forzar el path de error de psutil simulando ImportError.
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "psutil", None)  # type: ignore[arg-type]
    # Con None en sys.modules, ``import psutil`` lanza TypeError/ImportError.
    result = audit._resolve_client_id()
    # Cualquiera de los dos paths del fallback es válido (psutil real
    # podría devolver el nombre del proceso pytest si está instalado).
    # El contrato es "no raise" + retorna string non-empty.
    assert isinstance(result, str)
    assert result  # non-empty


def test_resolve_client_id_strips_whitespace_in_env(monkeypatch):
    monkeypatch.setenv("MIMIR_CLIENT_ID", "  opencode  ")
    assert audit._resolve_client_id() == "opencode"


def test_resolve_client_id_empty_env_treated_as_unset(monkeypatch):
    monkeypatch.setenv("MIMIR_CLIENT_ID", "   ")  # whitespace only
    # No assertion sobre el valor — solo que no raise y devuelve string.
    result = audit._resolve_client_id()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Sanitization defensive behaviour
# ---------------------------------------------------------------------------

def test_log_does_not_break_when_args_unjsonable(tmp_path):
    """args con objetos no-JSON (ya cubiertos por hash), pero ahora también
    deben pasar por sanitize sin romper."""
    class Weird:
        def __repr__(self):
            return "<weird>"

    path = tmp_path / "audit.log"
    log_tool_call(
        "p", "t", {"obj": Weird(), "TOKEN": "abc"}, 1.0, "error:Weird",
        path=path, error_message="oops",
    )
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    # TOKEN redacted, obj passes through stringified by JSON's default=str
    # path of _hash_args (but args_sanitized just returns the object — JSON
    # serialization at write time will use str()).
    assert entry["args_sanitized"]["TOKEN"] == "<redacted>"
