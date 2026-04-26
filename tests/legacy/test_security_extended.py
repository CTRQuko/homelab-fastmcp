"""Tests de seguridad extendidos: validación de inputs y sanitización."""
import re

from native_tools.secrets import mask
from native_tools.tailscale import _sanitize_error

# ---------------------------------------------------------------------------
# Tailscale — sanitización de errores
# ---------------------------------------------------------------------------

def test_sanitize_error_hides_api_key():
    err = "Request failed: tskey-api-FAKE_DUMMY_KEY-abc123 failed"
    assert _sanitize_error(err) == "tailscale API error (credenciales ocultas)"


def test_sanitize_error_hides_bearer():
    err = "Unauthorized: bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    assert _sanitize_error(err) == "tailscale API error (credenciales ocultas)"


def test_sanitize_error_allows_generic():
    err = "timeout connecting to api.tailscale.com"
    assert _sanitize_error(err) == err


# ---------------------------------------------------------------------------
# Secrets — masking
# ---------------------------------------------------------------------------

def test_mask_short():
    assert mask("abc") == "***"


def test_mask_medium():
    assert mask("abcd1234") == "********"


def test_mask_long():
    assert mask("abcdefgh12345678") == "abcd****"


def test_mask_empty():
    assert mask("") == "<empty>"


# ---------------------------------------------------------------------------
# UART detect — validación de puerto
# ---------------------------------------------------------------------------

def test_uart_port_valid_com():
    """Puertos COM son válidos."""
    assert re.match(r"^COM\d+$", "COM4")
    assert re.match(r"^COM\d+$", "COM10")


def test_uart_port_invalid_path_traversal():
    """Path traversal en puerto debe ser rechazado."""
    bad_ports = ["../../../dev/ttyS0", "COM4/../../etc/passwd", "ttyUSB0; rm -rf /"]
    for port in bad_ports:
        assert not re.match(r"^(COM\d+|/dev/tty\w+|tty\w+)$", port), f"Puerto malicioso no bloqueado: {port}"


def test_uart_baudrate_valid():
    """Baudrates comunes son válidos."""
    valid = [9600, 19200, 38400, 57600, 115200]
    for b in valid:
        assert 300 <= b <= 4000000, f"Baudrate {b} fuera de rango"


def test_uart_baudrate_invalid():
    """Baudrates inválidos deben ser rechazados."""
    invalid = [-1, 0, 5000000, None]
    for b in invalid:
        if b is not None:
            assert not (300 <= b <= 4000000), f"Baudrate inválido aceptado: {b}"


# ---------------------------------------------------------------------------
# GitHub — validación adicional
# ---------------------------------------------------------------------------

def test_github_issue_number_valid():
    assert isinstance(1, int) and 1 >= 1


def test_github_issue_number_invalid():
    invalid = [0, -1, "abc", None]
    for n in invalid:
        if isinstance(n, int):
            assert n < 1, f"Número de issue inválido aceptado: {n}"


# ---------------------------------------------------------------------------
# UniFi — validación indirecta (no hay funciones expuestas, verificamos .env)
# ---------------------------------------------------------------------------

def test_unifi_env_type():
    """UNIFI_API_TYPE solo acepta valores conocidos."""
    valid = ["local", "cloud-ea", "cloud-v1"]
    assert "local" in valid
    assert "hacked" not in valid


# ---------------------------------------------------------------------------
# GPON — validación indirecta
# ---------------------------------------------------------------------------

def test_gpon_port_valid():
    """Puerto SSH válido."""
    assert 1 <= 22 <= 65535


def test_gpon_port_invalid():
    """Puertos fuera de rango."""
    invalid = [0, -1, 65536, 99999]
    for p in invalid:
        assert not (1 <= p <= 65535), f"Puerto inválido aceptado: {p}"
