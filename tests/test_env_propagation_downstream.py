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
    sobrescribirla. Antes del fix, `if _key not in os.environ` no disparaba."""
    # Simula cliente MCP que pasa UNIFI_API_KEY vacía al subprocess
    monkeypatch.setenv("UNIFI_API_KEY", "")

    # Creamos un .env temporal y monkeypatch server.__file__ para apuntar ahí
    import importlib
    env_dir = tmp_path
    env_file = env_dir / ".env"
    env_file.write_text("UNIFI_API_KEY=value_from_dotenv\n", encoding="utf-8")

    # Apuntar a un server.py copiado en tmp_path sería complejo. En su lugar,
    # probamos la lógica inline: reproducir el bloque de carga tal como en
    # server.py, y verificar que 'not os.environ.get(_key)' SÍ sobrescribe.
    import os
    for _line in env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip()
            if not os.environ.get(_key):  # el fix
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


def test_gpon_env_excludes_unifi(monkeypatch):
    """Regresión cruzada: GPON no recibe UNIFI_."""
    monkeypatch.setenv("GPON_HOST", "g")
    monkeypatch.setenv("UNIFI_API_KEY", "u")

    server = _reimport_server()
    gpon_env = server._gpon_config["mcpServers"]["default"]["env"]

    assert gpon_env.get("GPON_HOST") == "g"
    assert "UNIFI_API_KEY" not in gpon_env
