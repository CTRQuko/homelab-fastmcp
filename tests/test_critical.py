"""Tests críticos post-auditoría: contratos, regresiones y seguridad defensiva.

Cubren las áreas peor cubiertas identificadas en el análisis:
- Prioridad de carga de secrets (contrato público de secrets.py)
- Parsing robusto de .env (bug R2)
- Propagación limpia de env vars a downstream (bug R4 + regresión UniFi 401)
- Sanitización defensiva de credenciales en errores (regresión)
- Límite de longitud de device_id (defensa DoS, bug R3)
"""
import sys

import pytest
import requests

# ---------------------------------------------------------------------------
# Test #1 — prioridad de secrets: env > .md > .env
# ---------------------------------------------------------------------------

def test_secrets_priority_env_over_md_over_dotenv(tmp_path, monkeypatch):
    """secrets.load() debe respetar la prioridad documentada:
    1. Environment variable
    2. $HOMELAB_DIR/.config/secrets/*.md
    3. .env en la raíz del proyecto
    """
    from native_tools import secrets as sec_mod

    # Preparar entorno aislado
    homelab_dir = tmp_path / "homelab"
    secrets_dir = homelab_dir / ".config" / "secrets"
    secrets_dir.mkdir(parents=True)
    md_file = secrets_dir / "test.md"
    md_file.write_text("TESTKEY_PRIO=from_md\n", encoding="utf-8")

    project_env = tmp_path / ".env"
    project_env.write_text("TESTKEY_PRIO=from_dotenv\n", encoding="utf-8")

    monkeypatch.setattr(sec_mod, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.setattr(sec_mod, "_PROJECT_ENV", project_env)

    # Caso 1: env var presente → gana
    monkeypatch.setenv("TESTKEY_PRIO", "from_env")
    assert sec_mod.load("TESTKEY_PRIO") == "from_env"

    # Caso 2: sin env var → .md gana
    monkeypatch.delenv("TESTKEY_PRIO")
    assert sec_mod.load("TESTKEY_PRIO") == "from_md"

    # Caso 3: sin env, sin .md → .env gana
    md_file.unlink()
    assert sec_mod.load("TESTKEY_PRIO") == "from_dotenv"

    # Caso 4: sin ninguna fuente → RuntimeError con mensaje útil
    project_env.unlink()
    with pytest.raises(RuntimeError, match="TESTKEY_PRIO"):
        sec_mod.load("TESTKEY_PRIO")


# ---------------------------------------------------------------------------
# Test #2 — parsing robusto de .env (bug R2)
# ---------------------------------------------------------------------------

def test_dotenv_parsing_handles_inline_comments():
    """`KEY=value # comment` debe dar 'value', no 'value # comment'."""
    from server import _parse_env_value

    assert _parse_env_value("value # comment") == "value"
    assert _parse_env_value("value\t# comment") == "value"


def test_dotenv_parsing_hash_without_space_is_part_of_value():
    """`KEY=value#notcomment` → 'value#notcomment' (hash sin espacio previo)."""
    from server import _parse_env_value
    assert _parse_env_value("value#notcomment") == "value#notcomment"


def test_dotenv_parsing_strips_quotes():
    """Valores entre comillas deben tener las comillas eliminadas."""
    from server import _parse_env_value
    assert _parse_env_value('"hello world"') == "hello world"
    assert _parse_env_value("'hello world'") == "hello world"


def test_dotenv_parsing_preserves_hash_inside_quotes():
    """`"value # inside"` → 'value # inside' (# no es comentario dentro de comillas)."""
    from server import _parse_env_value
    assert _parse_env_value('"value # inside"') == "value # inside"


def test_dotenv_parsing_empty_and_whitespace():
    from server import _parse_env_value
    assert _parse_env_value("") == ""
    assert _parse_env_value("   ") == ""
    assert _parse_env_value("  value  ") == "value"


def test_dotenv_parsing_preserves_equals_in_value():
    """URLs con query strings no se deben partir."""
    from server import _parse_env_value
    assert _parse_env_value("https://a.com?x=y") == "https://a.com?x=y"


# ---------------------------------------------------------------------------
# Test #4 — propagación de env vars a downstream (regresión UniFi 401)
# ---------------------------------------------------------------------------

def _reimport_server():
    """Re-importa server.py para que lea el os.environ actual."""
    mods_to_clear = [m for m in sys.modules if m == "server" or m.startswith("native_tools")]
    for m in mods_to_clear:
        del sys.modules[m]
    import server
    return server


def test_downstream_env_propagation_unifi_filters_empty(monkeypatch):
    """UNIFI_* con valor no vacío se propaga; UNIFI_* vacías NO se propagan."""
    monkeypatch.setenv("UNIFI_API_KEY", "test_key_123")
    monkeypatch.setenv("UNIFI_LOCAL_HOST", "10.0.0.1")
    monkeypatch.setenv("UNIFI_EMPTY_FIELD", "")

    server = _reimport_server()
    env = server._unifi_config["mcpServers"]["default"]["env"]

    assert env.get("UNIFI_API_KEY") == "test_key_123"
    assert env.get("UNIFI_LOCAL_HOST") == "10.0.0.1"
    assert "UNIFI_EMPTY_FIELD" not in env, (
        "env vars vacías NO deben propagarse al downstream "
        "(pueden confundir al proceso hijo con 'key definida pero vacía')"
    )


def test_downstream_env_propagation_no_cross_contamination(monkeypatch):
    """GPON_* no aparece en env de UniFi; UNIFI_* no aparece en env de GPON."""
    monkeypatch.setenv("UNIFI_API_KEY", "unifi_k")
    monkeypatch.setenv("GPON_HOST", "192.168.100.10")
    monkeypatch.setenv("GPON_USER", "testuser")

    server = _reimport_server()
    unifi_env = server._unifi_config["mcpServers"]["default"]["env"]
    gpon_env = server._gpon_config["mcpServers"]["default"]["env"]

    assert all(k.startswith("UNIFI_") for k in unifi_env), (
        f"env de UniFi tiene claves no-UNIFI: {[k for k in unifi_env if not k.startswith('UNIFI_')]}"
    )
    assert all(k.startswith("GPON_") for k in gpon_env), (
        f"env de GPON tiene claves no-GPON: {[k for k in gpon_env if not k.startswith('GPON_')]}"
    )
    assert "UNIFI_API_KEY" not in gpon_env
    assert "GPON_HOST" not in unifi_env


# ---------------------------------------------------------------------------
# Test #7 — sanitización end-to-end en cada endpoint de Tailscale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method_name,http_method", [
    ("list_devices", "get"),
    ("get_acls", "get"),
    ("get_dns", "get"),
])
def test_tailscale_endpoint_sanitizes_leaked_credentials(method_name, http_method, monkeypatch):
    """Si la excepción HTTP contiene tskey-api-*, la RuntimeError NO debe exponerla.
    Verifica que TODOS los endpoints aplican _sanitize_error consistentemente.
    """
    from native_tools import tailscale

    monkeypatch.setenv("TAILSCALE_API_KEY", "tskey-api-FAKEKEY-12345")
    monkeypatch.setenv("TAILSCALE_TAILNET", "example.com")

    def raise_with_key(*args, **kwargs):
        raise requests.exceptions.HTTPError(
            "401 Unauthorized: invalid token tskey-api-FAKEKEY-12345"
        )

    monkeypatch.setattr(requests, http_method, raise_with_key)

    with pytest.raises(RuntimeError) as exc_info:
        getattr(tailscale, method_name)()

    assert "tskey-api-FAKEKEY-12345" not in str(exc_info.value), (
        f"Fuga de credencial en {method_name}: {exc_info.value}"
    )
    assert "credenciales ocultas" in str(exc_info.value)


def test_tailscale_get_device_sanitizes_bearer_token(monkeypatch):
    """get_device con excepción que contiene 'Bearer <token>' → sanitizada."""
    from native_tools import tailscale

    monkeypatch.setenv("TAILSCALE_API_KEY", "tskey-api-SECRETABC")
    monkeypatch.setenv("TAILSCALE_TAILNET", "example.com")

    def raise_with_bearer(*args, **kwargs):
        raise requests.exceptions.RequestException(
            "Authorization header: Bearer tskey-api-SECRETABC rejected"
        )

    monkeypatch.setattr(requests, "get", raise_with_bearer)

    with pytest.raises(RuntimeError) as exc_info:
        tailscale.get_device("valid-id")

    assert "tskey-api-SECRETABC" not in str(exc_info.value)


def test_tailscale_authorize_device_sanitizes(monkeypatch):
    """authorize_device (POST) también sanitiza credenciales."""
    from native_tools import tailscale

    monkeypatch.setenv("TAILSCALE_API_KEY", "tskey-api-POSTKEY")
    monkeypatch.setenv("TAILSCALE_TAILNET", "example.com")

    def raise_with_token(*args, **kwargs):
        raise requests.exceptions.HTTPError(
            "403 Forbidden: token=tskey-api-POSTKEY invalid"
        )

    monkeypatch.setattr(requests, "post", raise_with_token)

    with pytest.raises(RuntimeError) as exc_info:
        tailscale.authorize_device("valid-id")

    assert "tskey-api-POSTKEY" not in str(exc_info.value)


def test_tailscale_delete_device_sanitizes(monkeypatch):
    """delete_device (DELETE) también sanitiza credenciales."""
    from native_tools import tailscale

    monkeypatch.setenv("TAILSCALE_API_KEY", "tskey-api-DELKEY")
    monkeypatch.setenv("TAILSCALE_TAILNET", "example.com")

    def raise_with_token(*args, **kwargs):
        raise requests.exceptions.RequestException(
            "DELETE failed: tskey-api-DELKEY auth error"
        )

    monkeypatch.setattr(requests, "delete", raise_with_token)

    with pytest.raises(RuntimeError) as exc_info:
        tailscale.delete_device("valid-id")

    assert "tskey-api-DELKEY" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test #3 — device_id length limit (defensa DoS)
# ---------------------------------------------------------------------------

def test_device_id_rejects_excessive_length():
    """device_id de 10000 chars debe ser rechazado (defensa contra payload bomb)."""
    from native_tools.tailscale import _validate_device_id
    with pytest.raises(ValueError, match="inválido"):
        _validate_device_id("a" * 10000)


def test_device_id_boundary_64_accepted():
    """64 caracteres: aceptado (límite máximo documentado)."""
    from native_tools.tailscale import _validate_device_id
    _validate_device_id("a" * 64)  # no debe lanzar


def test_device_id_boundary_65_rejected():
    """65 caracteres: rechazado (1 sobre el límite)."""
    from native_tools.tailscale import _validate_device_id
    with pytest.raises(ValueError):
        _validate_device_id("a" * 65)


def test_device_id_rejects_null_byte():
    """device_id con null byte: rechazado (defensa contra inyección)."""
    from native_tools.tailscale import _validate_device_id
    with pytest.raises(ValueError):
        _validate_device_id("abc\x00def")


# ---------------------------------------------------------------------------
# Test bonus — secrets.mask() nunca filtra más que 'visible' chars
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,visible,expected", [
    ("", 4, "<empty>"),
    ("a", 4, "*"),
    ("abcdef", 4, "******"),
    ("abcdefgh", 4, "********"),       # len == visible*2: todo asteriscos
    ("abcdefghij", 4, "abcd****"),     # len > visible*2: primeros 4 + ****
    ("x" * 100, 4, "xxxx****"),
])
def test_mask_contract(value, visible, expected):
    """mask() debe cumplir: si len(value) <= visible*2 → todo *, si no → primeros N + ****."""
    from native_tools.secrets import mask
    assert mask(value, visible=visible) == expected
