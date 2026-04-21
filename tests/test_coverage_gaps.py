"""Tests de gaps de cobertura — áreas peor cubiertas tras auditoría v2.

Enfoque: uart_detect.py (169 líneas casi sin cobertura), secrets loading
sobre ficheros reales, degradación silenciosa de github, artefactos muertos.
"""
import json
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# T1, T2, T3 — uart_detect.py (mocks de pyserial)
# ---------------------------------------------------------------------------

class _FakeSerial:
    """Mock mínimo de serial.Serial para tests deterministas."""

    def __init__(self, boot_bytes: bytes = b"", responses: dict | None = None):
        self._boot = boot_bytes
        self._boot_consumed = False
        self._responses = responses or {}
        self._pending = b""
        self._last_cmd = ""
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._pending) if self._pending else 0

    def read(self, n: int) -> bytes:
        data, self._pending = self._pending[:n], self._pending[n:]
        return data

    def write(self, data: bytes) -> int:
        self._last_cmd = data.decode("utf-8", errors="replace").strip()
        # Enqueue response for the command that was written
        if self._last_cmd in self._responses:
            self._pending = self._responses[self._last_cmd].encode("utf-8")
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        # First call: feed boot greeting into pending
        if not self._boot_consumed:
            self._pending = self._boot
            self._boot_consumed = True

    def reset_output_buffer(self):
        pass

    def close(self):
        self.closed = True


def _patch_uart(monkeypatch, port="COM_TEST", fake_serial: _FakeSerial | None = None):
    """Patch serial.tools.list_ports.comports + serial.Serial."""
    import serial.tools.list_ports

    class P:
        device = port

    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [P()])

    import serial as pyserial
    fs = fake_serial or _FakeSerial()
    monkeypatch.setattr(pyserial, "Serial", lambda **kwargs: fs)
    return fs


def test_uart_detect_parses_uboot_greeting(monkeypatch):
    """T1: Boot greeting con 'U-Boot' → detecta bootloader y hace early return."""
    from native_tools.uart_detect import detectar_dispositivo_uart

    fake = _FakeSerial(boot_bytes=b"U-Boot 2020.01 (Mar 01 2023)\nautoboot in 3s\n")
    _patch_uart(monkeypatch, port="COM_TEST", fake_serial=fake)

    result = detectar_dispositivo_uart("COM_TEST", baudrate=115200, timeout_cmd=0.1)

    assert result["conectado"] is True
    assert "U-Boot" in result["dispositivo"]
    assert fake._last_cmd == "", "No debe ejecutar comandos si detecta U-Boot en greeting"


def test_uart_detect_handles_silent_device_with_bounded_timeout(monkeypatch):
    """T2: dispositivo silencioso → termina en tiempo acotado."""
    import time as time_mod

    from native_tools.uart_detect import detectar_dispositivo_uart

    fake = _FakeSerial(boot_bytes=b"", responses={})
    _patch_uart(monkeypatch, port="COM_SILENT", fake_serial=fake)

    t0 = time_mod.monotonic()
    result = detectar_dispositivo_uart("COM_SILENT", baudrate=115200, timeout_cmd=0.1)
    elapsed = time_mod.monotonic() - t0

    # Hay 8 comandos × timeout_cmd + ~1.5s de boot wait + margen
    assert elapsed < 5.0, f"UART tardó {elapsed:.1f}s con timeout_cmd=0.1 (esperado <5s)"
    assert result["sistema"] is None
    assert result["conectado"] is True
    assert any("no respondió" in n.lower() for n in result["notas"]) or \
           result["dispositivo"] == "desconocido"


def test_uart_detect_cleanup_preserves_hostname_output(monkeypatch):
    """T3: cleanup de eco no debe descartar output legítimo que empieza por el cmd.

    Bug R20: `uart_detect.py:115` descarta líneas que empiecen por `cmd.split()[0]`
    y sean cortas. Hostname "hostname" tras comando "hostname" podría descartarse.
    """
    from native_tools.uart_detect import detectar_dispositivo_uart

    # Simular: device responde al cmd "hostname" con "hostname\nrouter1\n"
    # (primera línea = eco del cmd, segunda = respuesta real)
    responses = {"hostname": "hostname\nrouter1\n"}
    fake = _FakeSerial(boot_bytes=b"", responses=responses)
    _patch_uart(monkeypatch, port="COM_HOST", fake_serial=fake)

    result = detectar_dispositivo_uart("COM_HOST", baudrate=115200, timeout_cmd=0.1)

    # router1 NO debe ser descartado por el filtro de eco
    assert result["hostname"] == "router1", (
        f"hostname='router1' debería extraerse, pero se obtuvo: {result}"
    )


# ---------------------------------------------------------------------------
# T4 — github degradación anónima silenciosa
# ---------------------------------------------------------------------------

def test_github_client_anonymous_emits_warning(monkeypatch):
    """T4: si GITHUB_TOKEN no existe, _client() debe emitir warning sobre rate limit."""
    from native_tools import github as gh_mod

    # Limpiar todas las fuentes de GITHUB_TOKEN
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Forzar que secrets.load() falle para GITHUB_TOKEN
    def fake_load(key):
        raise RuntimeError(f"Secret '{key}' not found")

    monkeypatch.setattr(gh_mod, "_load_secret", fake_load)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            gh_mod._client()
        except Exception:
            pass  # Si PyGithub() sin token lanza, no importa para este test

        msgs = [str(wi.message) for wi in w if issubclass(wi.category, UserWarning)]
        assert any("anonymous" in m.lower() or "rate limit" in m.lower() for m in msgs), (
            f"Esperado warning sobre degradación anónima, obtenido: {msgs}"
        )


# ---------------------------------------------------------------------------
# T5, T6, T7 — secrets edge cases sobre ficheros reales
# ---------------------------------------------------------------------------

def test_secrets_md_file_multiple_keys_returns_correct(tmp_path, monkeypatch):
    """T5: un .md con varias claves devuelve la correcta, no la primera."""
    from native_tools import secrets as sec

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "multi.md").write_text(
        "KEY_A=value_a\nKEY_B=value_b\nKEY_C=value_c\n", encoding="utf-8"
    )
    monkeypatch.setattr(sec, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.setattr(sec, "_PROJECT_ENV", tmp_path / "nonexistent.env")
    monkeypatch.delenv("KEY_A", raising=False)
    monkeypatch.delenv("KEY_B", raising=False)
    monkeypatch.delenv("KEY_C", raising=False)

    assert sec.load("KEY_A") == "value_a"
    assert sec.load("KEY_B") == "value_b"
    assert sec.load("KEY_C") == "value_c"


def test_secrets_md_file_malformed_does_not_crash(tmp_path, monkeypatch):
    """T6: .md con líneas sin '=' o con markdown real no rompe el loader."""
    from native_tools import secrets as sec

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "notes.md").write_text(
        "# Mis notas\n"
        "\n"
        "Esto es una nota sobre cosas.\n"
        "- item 1\n"
        "- item 2\n"
        "\n"
        "LINE_WITHOUT_EQUALS\n"
        "VALID_KEY=valid_value\n"
        "# Comment with = inside\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sec, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.setattr(sec, "_PROJECT_ENV", tmp_path / "nonexistent.env")
    monkeypatch.delenv("VALID_KEY", raising=False)

    assert sec.load("VALID_KEY") == "valid_value"


def test_secrets_error_message_does_not_leak_other_values(tmp_path, monkeypatch):
    """T7: mensaje de error incluye la key pedida pero no los valores de otras keys."""
    from native_tools import secrets as sec

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "s.md").write_text("OTHER_KEY=OTHER_SECRET_VALUE\n", encoding="utf-8")
    monkeypatch.setattr(sec, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.setattr(sec, "_PROJECT_ENV", tmp_path / "none.env")
    monkeypatch.delenv("NONEXISTENT_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        sec.load("NONEXISTENT_KEY")

    msg = str(exc_info.value)
    assert "NONEXISTENT_KEY" in msg  # OK incluir la clave pedida
    assert "OTHER_SECRET_VALUE" not in msg, (
        "Error message no debe exponer valores de otras keys"
    )


# ---------------------------------------------------------------------------
# T8 — .env edge case: comilla sin cerrar
# ---------------------------------------------------------------------------

def test_dotenv_parsing_unclosed_quote_documented_behavior():
    """T8: '\"unclosed' devuelve 'unclosed' (drop leading quote, sin closing)."""
    from server import _parse_env_value

    assert _parse_env_value('"unclosed') == "unclosed"
    assert _parse_env_value("'unclosed") == "unclosed"


# ---------------------------------------------------------------------------
# T9 — GitHub validación: nombres reales GitHub-inválidos
# ---------------------------------------------------------------------------

def test_github_name_rejects_leading_dot():
    """T9a: nombres que empiezan por '.' son inválidos en GitHub (rechazar local)."""
    from native_tools.github import _validate_name

    with pytest.raises(ValueError):
        _validate_name(".hidden")


def test_github_name_rejects_leading_hyphen():
    """T9b: nombres que empiezan por '-' son inválidos en GitHub."""
    from native_tools.github import _validate_name

    with pytest.raises(ValueError):
        _validate_name("-start")


def test_github_name_accepts_valid_names():
    """T9c: nombres válidos siguen pasando (no hay regresión)."""
    from native_tools.github import _validate_name

    for name in ["user", "user-name", "user_name", "user.name", "a1b2c3", "Org"]:
        _validate_name(name)  # no debe lanzar


# ---------------------------------------------------------------------------
# T10 — downstream/servers.json sincronización con server.py
# ---------------------------------------------------------------------------

def test_servers_json_in_sync_or_removed():
    """T10: si servers.json existe, sus namespaces coinciden con server.py;
    si no existe, passthrough.
    """
    project_root = Path(__file__).resolve().parent.parent
    servers_json = project_root / "downstream" / "servers.json"

    if not servers_json.exists():
        pytest.skip("downstream/servers.json no existe — OK, artefacto eliminado")

    data = json.loads(servers_json.read_text(encoding="utf-8"))
    json_namespaces = set(data.get("servers", {}).keys())

    # Namespaces documentados en server.py
    server_py = (project_root / "server.py").read_text(encoding="utf-8")
    # Extraer namespaces desde las llamadas mcp.mount(..., namespace="X")
    import re
    py_namespaces = set(re.findall(r'namespace="([^"]+)"', server_py))

    # servers.json debe ser subconjunto de server.py (puede faltar windows/docker si no son windows)
    missing_in_py = json_namespaces - py_namespaces
    assert not missing_in_py, (
        f"servers.json tiene namespaces que no están en server.py: {missing_in_py}. "
        f"Ambos deben mantenerse en sync o eliminar servers.json."
    )


# ---------------------------------------------------------------------------
# T11 — R17 warning en .md con claves duplicadas
# ---------------------------------------------------------------------------

def test_secrets_md_duplicate_keys_emits_warning(tmp_path, monkeypatch):
    """R17: si una clave aparece con valores distintos en .md, emitir UserWarning."""
    from native_tools import secrets as sec

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "a.md").write_text("DUP_KEY=value_1\n", encoding="utf-8")
    (secrets_dir / "b.md").write_text("DUP_KEY=value_2\n", encoding="utf-8")

    monkeypatch.setattr(sec, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.setattr(sec, "_PROJECT_ENV", tmp_path / "nonexistent.env")
    monkeypatch.delenv("DUP_KEY", raising=False)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        value = sec.load("DUP_KEY")
        msgs = [str(wi.message) for wi in w if issubclass(wi.category, UserWarning)]

    # Cualquiera de los dos valores es aceptable (depende del orden de glob)
    assert value in ("value_1", "value_2")
    assert any("DUP_KEY" in m and "2 veces" in m for m in msgs), (
        f"Esperado warning de claves duplicadas, obtenido: {msgs}"
    )


def test_secrets_md_duplicate_same_value_no_warning(tmp_path, monkeypatch):
    """Si la clave se repite con el MISMO valor, no hay warning (no es conflicto)."""
    from native_tools import secrets as sec

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "a.md").write_text("SAME_KEY=same_value\n", encoding="utf-8")
    (secrets_dir / "b.md").write_text("SAME_KEY=same_value\n", encoding="utf-8")

    monkeypatch.setattr(sec, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.setattr(sec, "_PROJECT_ENV", tmp_path / "nonexistent.env")
    monkeypatch.delenv("SAME_KEY", raising=False)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        value = sec.load("SAME_KEY")
        dup_warnings = [str(wi.message) for wi in w
                        if issubclass(wi.category, UserWarning) and "duplicad" in str(wi.message).lower()]

    assert value == "same_value"
    assert not dup_warnings


# ---------------------------------------------------------------------------
# T12 — R14 UART shell prompt detection: reducir falsos positivos
# ---------------------------------------------------------------------------

def test_uart_shell_prompt_not_detected_in_url_middle(monkeypatch):
    """R14: un '#' en medio de texto (URL anchor, path) no debe activar 'prompt detectado'."""
    from native_tools.uart_detect import detectar_dispositivo_uart

    # Boot greeting con '#' en medio (ej. URL con anchor)
    boot = b"Visita https://ejemplo.com/docs#install para mas info.\n"
    fake = _FakeSerial(boot_bytes=boot)
    _patch_uart(monkeypatch, port="COM_URL", fake_serial=fake)

    result = detectar_dispositivo_uart("COM_URL", baudrate=115200, timeout_cmd=0.05)

    prompt_notes = [n for n in result["notas"] if "prompt de shell activo" in n]
    assert not prompt_notes, (
        f"URL con '#' en medio no debe detectarse como shell prompt. notas={result['notas']}"
    )


def test_uart_shell_prompt_detected_at_line_end(monkeypatch):
    """R14: un '#' o '$' al final de la última línea SÍ debe detectarse como prompt."""
    from native_tools.uart_detect import detectar_dispositivo_uart

    # Boot greeting con prompt real al final
    boot = b"BusyBox v1.30 built-in shell\nWelcome to OpenWrt\nroot@router:/# "
    fake = _FakeSerial(boot_bytes=boot)
    _patch_uart(monkeypatch, port="COM_SHELL", fake_serial=fake)

    result = detectar_dispositivo_uart("COM_SHELL", baudrate=115200, timeout_cmd=0.05)

    prompt_notes = [n for n in result["notas"] if "prompt de shell activo" in n]
    assert prompt_notes, (
        f"Prompt '#' al final de línea debe detectarse. notas={result['notas']}"
    )


# ---------------------------------------------------------------------------
# T13 — R10 UART line_ending parameter
# ---------------------------------------------------------------------------

def test_uart_line_ending_default_is_lf(monkeypatch):
    """R10: default line_ending es '\\n' (retrocompat)."""
    from native_tools.uart_detect import detectar_dispositivo_uart

    responses = {"uname -a": "Linux router 5.10\n"}
    fake = _FakeSerial(boot_bytes=b"", responses=responses)
    _patch_uart(monkeypatch, port="COM_LF", fake_serial=fake)

    result = detectar_dispositivo_uart("COM_LF", baudrate=115200, timeout_cmd=0.05)

    # El mock responde a "uname -a" exactamente; con "\n" llega "uname -a\n"
    # que al strip es "uname -a". Si pasara \r\n, sería "uname -a\r" → no match.
    # Si el resultado capta Linux → line ending default funcionó.
    assert result["sistema"] == "Linux"


def test_uart_line_ending_crlf_sends_carriage_return(monkeypatch):
    """R10: line_ending='\\r\\n' debe enviar CR+LF al dispositivo."""
    from native_tools.uart_detect import detectar_dispositivo_uart

    # Mock que solo responde si el cmd termina con \r (ignora \n)
    class _CRLFSerial(_FakeSerial):
        def write(self, data: bytes) -> int:
            decoded = data.decode("utf-8", errors="replace")
            # Solo responder si contiene \r
            if "\r" in decoded:
                self._last_cmd = decoded.rstrip("\r\n")
                if self._last_cmd in self._responses:
                    self._pending = self._responses[self._last_cmd].encode()
            return len(data)

    responses = {"uname -a": "Linux crlf-device 5.4\n"}
    fake = _CRLFSerial(boot_bytes=b"", responses=responses)
    _patch_uart(monkeypatch, port="COM_CRLF", fake_serial=fake)

    result = detectar_dispositivo_uart(
        "COM_CRLF", baudrate=115200, timeout_cmd=0.05, line_ending="\r\n"
    )

    assert result["sistema"] == "Linux", (
        f"Dispositivo CRLF-only debe responder con line_ending='\\r\\n'. result={result}"
    )
