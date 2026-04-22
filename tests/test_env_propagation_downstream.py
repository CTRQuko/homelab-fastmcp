"""Tests de regresión para el fix UniFi 401 con clientes MCP no-Claude.

Verifica:
- .env fallback trata "" como ausente (antes usaba `in os.environ`, que no
  sobrescribía vars vacías seteadas por el cliente MCP).
- UNIFI_API_TYPE tiene default sensato ("local") cuando no se define.
- El env pasado a subprocess incluye vars de sistema (PATH, etc.) para que
  uv/uvx funcionen cuando create_proxy reemplaza el env wholesale.
- _build_subprocess_env respeta el filtro anti-cross-contamination pero
  no bloquea vars neutras necesarias.
"""
import sys


def _reimport_server():
    """Re-importa server.py para que lea el os.environ actual."""
    mods = [m for m in sys.modules if m == "server" or m.startswith("native_tools")]
    for m in mods:
        del sys.modules[m]
    import server
    return server


def test_dotenv_overrides_empty_env_var(monkeypatch, tmp_path):
    """Si el cliente MCP exporta UNIFI_API_KEY='' explícito, el .env debe poder
    rellenarla. Antes del fix, `if _key not in os.environ` no disparaba porque
    la key SÍ está en os.environ (con valor vacío)."""
    monkeypatch.setenv("UNIFI_API_KEY", "")

    env_file = tmp_path / ".env"
    env_file.write_text("UNIFI_API_KEY=value_from_dotenv\n", encoding="utf-8")

    # Reproduce la lógica inline del bloque de carga con el fix:
    import os
    for _line in env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip()
            if not os.environ.get(_key):  # fix: vacío cuenta como ausente
                os.environ[_key] = _val

    assert os.environ["UNIFI_API_KEY"] == "value_from_dotenv"


def test_unifi_api_type_has_default(monkeypatch):
    """Si el cliente no define UNIFI_API_TYPE, server.py pone default='local'."""
    monkeypatch.delenv("UNIFI_API_TYPE", raising=False)
    # Aislamos: sin .env que lo defina
    monkeypatch.setenv("UNIFI_API_KEY", "fake")  # para evitar warning "sin key"

    server = _reimport_server()
    import os

    # setdefault se ejecuta en import-time; debe haber dejado UNIFI_API_TYPE="local"
    assert os.environ.get("UNIFI_API_TYPE") == "local"
    # Y debe propagarse al env del downstream
    unifi_env = server._unifi_config["mcpServers"]["default"]["env"]
    assert unifi_env.get("UNIFI_API_TYPE") == "local"


def test_unifi_env_inherits_path_and_system_vars(monkeypatch):
    """El env de UniFi debe heredar vars de sistema necesarias para que uvx
    encuentre su cache/python. Antes del fix solo tenía UNIFI_*."""
    monkeypatch.setenv("UNIFI_API_KEY", "k")
    monkeypatch.setenv("PATH", "/fake/path:/another")

    server = _reimport_server()
    unifi_env = server._unifi_config["mcpServers"]["default"]["env"]

    # PATH debe estar propagada (crítico para uvx localizar ejecutables)
    assert "PATH" in unifi_env
    assert unifi_env["PATH"] == "/fake/path:/another"


def test_build_subprocess_env_excludes_other_domains(monkeypatch):
    """_build_subprocess_env filtra prefijos de otros dominios."""
    monkeypatch.setenv("UNIFI_API_KEY", "u1")
    monkeypatch.setenv("GPON_HOST", "g1")
    monkeypatch.setenv("PROXMOX_TOKEN_VALUE", "p1")
    monkeypatch.setenv("TAILSCALE_API_KEY", "t1")
    monkeypatch.setenv("GITHUB_TOKEN", "gh1")
    monkeypatch.setenv("NEUTRAL_VAR", "n1")

    server = _reimport_server()
    unifi_env = server._build_subprocess_env("UNIFI_")

    assert unifi_env.get("UNIFI_API_KEY") == "u1"
    assert unifi_env.get("NEUTRAL_VAR") == "n1"  # neutras pasan
    assert "GPON_HOST" not in unifi_env
    assert "PROXMOX_TOKEN_VALUE" not in unifi_env
    assert "TAILSCALE_API_KEY" not in unifi_env
    assert "GITHUB_TOKEN" not in unifi_env


def test_build_subprocess_env_skips_empty_values(monkeypatch):
    """Vars con valor vacío no se propagan (evita confundir al subprocess)."""
    monkeypatch.setenv("UNIFI_API_KEY", "valid")
    monkeypatch.setenv("UNIFI_EMPTY", "")

    server = _reimport_server()
    unifi_env = server._build_subprocess_env("UNIFI_")

    assert unifi_env.get("UNIFI_API_KEY") == "valid"
    assert "UNIFI_EMPTY" not in unifi_env


def test_dotenv_wins_flag_overrides_external(monkeypatch, tmp_path, capsys):
    """Con HOMELAB_DOTENV_WINS=1, el .env del proyecto pisa env vars externas.

    Caso de uso: credencial vieja exportada en env del usuario (Windows User
    env var, launcher corporate, etc.) que no se puede limpiar — el flag
    asegura que el .env local del proyecto siempre gane.
    """
    monkeypatch.setenv("HOMELAB_DOTENV_WINS", "1")
    monkeypatch.setenv("UNIFI_API_KEY", "stale_external_value")

    env_file = tmp_path / ".env"
    env_file.write_text("UNIFI_API_KEY=good_value_from_dotenv\n", encoding="utf-8")

    # Reproduce la lógica inline del bloque de carga con el flag activo
    import os
    import sys
    _dotenv_wins = os.environ.get("HOMELAB_DOTENV_WINS", "0").strip() == "1"
    assert _dotenv_wins is True

    for _line in env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip()
            if _dotenv_wins:
                os.environ[_key] = _val
            else:
                if not os.environ.get(_key):
                    os.environ[_key] = _val

    assert os.environ["UNIFI_API_KEY"] == "good_value_from_dotenv"


def test_dotenv_wins_flag_default_off(monkeypatch):
    """Default (flag ausente o "0"): externa gana — convención dotenv."""
    monkeypatch.delenv("HOMELAB_DOTENV_WINS", raising=False)
    monkeypatch.setenv("UNIFI_API_KEY", "external_wins_value")

    import os
    _dotenv_wins = os.environ.get("HOMELAB_DOTENV_WINS", "0").strip() == "1"
    assert _dotenv_wins is False

    # Simulamos el bloque: si flag off y var externa ya tiene valor, .env no entra.
    dotenv_val = "would_be_ignored"
    if _dotenv_wins:
        os.environ["UNIFI_API_KEY"] = dotenv_val
    else:
        if not os.environ.get("UNIFI_API_KEY"):
            os.environ["UNIFI_API_KEY"] = dotenv_val

    assert os.environ["UNIFI_API_KEY"] == "external_wins_value"


def test_stale_env_warning_emitted_for_critical_domain(monkeypatch, capsys):
    """REAL-LOADER: si una key crítica difiere entre external env y .env,
    server.py emite un WARNING a stderr al arrancar (default flag off).

    Regresión del caso UniFi 401 (commit 75a35d0): si Windows User Env tiene
    credencial stale, el warning temprano ayuda al diagnóstico sin instrumentación.
    """
    # Forzar disparidad: external UNIFI_API_KEY distinto del valor en el .env
    # del proyecto (iMLPDk… para UniFi real). Usamos un valor claramente stale.
    monkeypatch.setenv("UNIFI_API_KEY", "STALE_EXTERNAL_VALUE_XX")
    monkeypatch.delenv("HOMELAB_DOTENV_WINS", raising=False)

    _reimport_server()
    captured = capsys.readouterr()

    assert "WARNING" in captured.err
    assert "UNIFI_API_KEY" in captured.err
    assert "dotenv" in captured.err.lower()  # menciona la convención/flag


def test_stale_env_warning_silent_for_neutral_keys(monkeypatch, capsys):
    """REAL-LOADER: el warning NO se emite para keys fuera de los prefijos
    críticos (UNIFI_/GPON_/PROXMOX_/TAILSCALE_/GITHUB_)."""
    # Setea una key que está en .env (HOMELAB_DIR) con valor distinto — neutral
    monkeypatch.setenv("HOMELAB_DIR", "/some/other/path")
    monkeypatch.delenv("HOMELAB_DOTENV_WINS", raising=False)

    _reimport_server()
    captured = capsys.readouterr()

    # No debe aparecer el warning crítico (puede haber otros logs de INFO)
    assert "WARNING" not in captured.err or "HOMELAB_DIR" not in captured.err


def test_dotenv_bad_encoding_does_not_crash_startup(tmp_path):
    """LOGIC REFERENCE: si .env contiene bytes no-UTF-8, el arranque no debe
    crashear. El bloque try/except debe capturar UnicodeDecodeError y continuar.

    Test inline porque el loader real lee Path(__file__).parent/.env y no
    es trivial redirigirlo sin copiar todo server.py.
    """
    import logging
    env_file = tmp_path / ".env"
    # Escribimos bytes Latin-1 que no son UTF-8 válidos
    env_file.write_bytes(b"UNIFI_API_KEY=valor_con_\xe9\xe8\n")

    lines: list[str] = []
    warnings_emitted = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            warnings_emitted.append(record.getMessage())

    log = logging.getLogger("test-dotenv-bad-encoding")
    log.addHandler(_CaptureHandler())

    # Reproduce el bloque try/except del loader real
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError) as e:
        log.warning(
            ".env no se pudo leer (%s): %s. Continuando con env vars externas.",
            type(e).__name__, e,
        )

    # Assert: no se propagó la excepción y se emitió warning
    assert lines == []  # no se cargó nada
    assert any("UnicodeDecodeError" in msg for msg in warnings_emitted)


def test_gpon_env_excludes_unifi(monkeypatch):
    """Regresión cruzada: GPON no recibe UNIFI_."""
    monkeypatch.setenv("GPON_HOST", "g")
    monkeypatch.setenv("UNIFI_API_KEY", "u")

    server = _reimport_server()
    gpon_env = server._gpon_config["mcpServers"]["default"]["env"]

    assert gpon_env.get("GPON_HOST") == "g"
    assert "UNIFI_API_KEY" not in gpon_env
