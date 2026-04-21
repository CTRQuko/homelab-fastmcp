"""Homelab FastMCP Aggregator.

Reemplaza al mcp-router Go con FastMCP Python.
Monta downstream MCPs con namespacing automático.
"""
import logging
import os
import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server import create_proxy

# ---------------------------------------------------------------------------
# Logging (stderr, formato conciso — stdout está reservado al protocolo MCP)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("HOMELAB_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("homelab-fastmcp")

# ---------------------------------------------------------------------------
# .env parsing helpers
# ---------------------------------------------------------------------------

def _parse_env_value(raw: str) -> str:
    """Parse a raw .env value, handling quotes and inline comments.

    Rules:
    - Leading/trailing whitespace is stripped.
    - If the value starts with a quote (' or "), everything up to the matching
      quote is preserved verbatim (including '#' characters).
    - Unquoted values: '#' preceded by whitespace (space or tab) starts an
      inline comment and is stripped.
    - '#' without a leading space is treated as part of the value.
    """
    raw = raw.strip()
    if not raw:
        return ""

    # Quoted value: take everything between the first pair of matching quotes
    if raw[0] in ('"', "'"):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end > 0:
            return raw[1:end]
        # Malformed (no closing quote): drop the leading quote, return rest
        return raw[1:]

    # Unquoted: strip inline comment only when '#' is preceded by whitespace
    for sep in (" #", "\t#"):
        idx = raw.find(sep)
        if idx >= 0:
            raw = raw[:idx]
            break
    return raw.strip()


# ---------------------------------------------------------------------------
# Load .env if present (development convenience)
# ---------------------------------------------------------------------------
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _parse_env_value(_val)
            if _key not in os.environ:
                os.environ[_key] = _val

# ---------------------------------------------------------------------------
# Homelab directory (cross-platform)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _default_homelab = "C:/homelab"
else:
    _default_homelab = "/home"  # Linux/macOS fallback
HOMELAB_DIR = os.environ.get("HOMELAB_DIR", _default_homelab)

# ---------------------------------------------------------------------------
# Downstream env vars (injected into child processes)
# ---------------------------------------------------------------------------
# UniFi
os.environ.setdefault("UNIFI_API_KEY", "")
os.environ.setdefault("UNIFI_LOCAL_HOST", "192.168.1.12")
os.environ.setdefault("UNIFI_LOCAL_PORT", "11443")
os.environ.setdefault("UNIFI_LOCAL_VERIFY_SSL", "false")

# GPON
os.environ.setdefault("GPON_HOST", "192.168.100.10")
os.environ.setdefault("GPON_USER", "root")
os.environ.setdefault("GPON_PASS", "")
os.environ.setdefault("GPON_PORT", "22")
os.environ.setdefault("GPON_TIMEOUT", "10")

_ON_WINDOWS = sys.platform == "win32"

_instructions = """
Agrega herramientas de infraestructura homelab.

AYUDA PARA ELEGIR HERRAMIENTAS:

- linux: operaciones de fichero y comandos SSH (solo lectura)
- proxmox: gestión de VMs, LXCs, nodos
- unifi: red UniFi, VLANs, clientes
"""
if _ON_WINDOWS:
    _instructions += """
- windows: operaciones de fichero y PowerShell (solo lectura)
- docker: contenedores e imágenes
"""

_instructions += """
GPON vs UART — IMPORTANTE:

- gpon: Gestión COMPLETA de sticks GPON (preferido siempre que sea posible).
  → Para operaciones normales (status, configuración, reinicio, backup):
    usar gpon_* vía SSH (red).
  → Para recovery físico (U-Boot, flasheo, desbrickeo):
    usar gpon_uart_* (puerto serie físico al chip del stick).
  → gpon_uart_* está especializado para sticks GPON (Lantiq, U-Boot, etc.).

- uart: UART/Serie GENÉRICO para CUALQUIER dispositivo embebido.
  → Puertos COM/ttyUSB, comandos shell genéricos, lectura/escritura raw.
  → NO usar uart_* para gestión normal de un stick GPON; usar gpon_* (SSH).
  → Solo usar uart_* si el dispositivo NO es un stick GPON, o si se necesita
    comunicación serie genérica no especializada.

DETECCIÓN DE DISPOSITIVOS EN PUERTOS SERIE:
→ Cuando el usuario pregunte "¿qué hay en COMx?" o "identifica dispositivo en puerto y":
  USAR PRIMERO uart_detectar_dispositivo(puerto). Esta tool conecta, interroga y reporta.
  NO quedarse en "hay un adaptador USB" — el usuario quiere saber qué dispositivo está
  al otro lado del cable.

REGLA DE ORO:
Si el objetivo es un stick GPON y funciona por red → usar gpon_* (SSH).
Si el stick GPON está brickeado o en recovery físico → usar gpon_uart_*.
uart_* es solo para dispositivos que NO son sticks GPON.
"""

log.info(
    "Homelab Aggregator iniciando — platform=%s HOMELAB_DIR=%s",
    sys.platform, HOMELAB_DIR,
)
mcp = FastMCP("Homelab Aggregator", instructions=_instructions)

# ---------------------------------------------------------------------------
# Downstream mounts
# ---------------------------------------------------------------------------

if _ON_WINDOWS:
    _windows_config = {
        "mcpServers": {
            "default": {
                "command": "uv",
                "args": [
                    "--directory",
                    f"{HOMELAB_DIR}/mcp-servers/homelab-mcp",
                    "run",
                    "homelab-windows-mcp"
                ]
            }
        }
    }
    mcp.mount(create_proxy(_windows_config), namespace="windows")
    log.info("Downstream montado: windows")

_linux_config = {
    "mcpServers": {
        "default": {
            "command": "uv",
            "args": [
                "--directory",
                f"{HOMELAB_DIR}/mcp-servers/homelab-mcp",
                "run",
                "homelab-linux-mcp"
            ]
        }
    }
}
mcp.mount(create_proxy(_linux_config), namespace="linux")
log.info("Downstream montado: linux")

_proxmox_config = {
    "mcpServers": {
        "default": {
            "command": "uv",
            "args": [
                "--directory",
                f"{HOMELAB_DIR}/mcp-servers/homelab-mcp",
                "run",
                "homelab-proxmox-mcp"
            ]
        }
    }
}
mcp.mount(create_proxy(_proxmox_config), namespace="proxmox")
log.info("Downstream montado: proxmox")

if _ON_WINDOWS:
    _docker_config = {
        "mcpServers": {
            "default": {
                "command": "uv",
                "args": [
                    "--directory",
                    f"{HOMELAB_DIR}/mcp-servers/homelab-mcp",
                    "run",
                    "homelab-docker-mcp"
                ]
            }
        }
    }
    mcp.mount(create_proxy(_docker_config), namespace="docker")
    log.info("Downstream montado: docker")

_unifi_config = {
    "mcpServers": {
        "default": {
            "command": "uvx",
            "args": ["unifi-mcp-server"],
            "env": {
                k: v for k, v in os.environ.items()
                if k.startswith("UNIFI_") and v  # no propagar valores vacíos
            },
        }
    }
}
mcp.mount(create_proxy(_unifi_config), namespace="unifi")
_unifi_env_keys = [k for k in _unifi_config["mcpServers"]["default"]["env"]]
log.info("Downstream montado: unifi (env keys: %s)", sorted(_unifi_env_keys))
if not _unifi_env_keys:
    log.warning("unifi: ninguna variable UNIFI_* con valor — downstream puede fallar")

_uart_config = {
    "mcpServers": {
        "default": {
            "command": "uv",
            "args": [
                "--directory",
                f"{HOMELAB_DIR}/mcp-servers/mcp-uart-serial",
                "run",
                "uart-mcp"
            ]
        }
    }
}
mcp.mount(create_proxy(_uart_config), namespace="uart")
log.info("Downstream montado: uart")

_gpon_config = {
    "mcpServers": {
        "default": {
            "command": "uv",
            "args": [
                "--directory",
                f"{HOMELAB_DIR}/mcp-servers/gpon-mcp",
                "run",
                "gpon-mcp"
            ],
            "env": {
                k: v for k, v in os.environ.items()
                if k.startswith("GPON_") and v  # no propagar valores vacíos
            },
        }
    }
}
mcp.mount(create_proxy(_gpon_config), namespace="gpon")
_gpon_env_keys = [k for k in _gpon_config["mcpServers"]["default"]["env"]]
log.info("Downstream montado: gpon (env keys: %s)", sorted(_gpon_env_keys))

# ---------------------------------------------------------------------------
# Native tools (UART device detection)
# ---------------------------------------------------------------------------

from native_tools.uart_detect import detectar_dispositivo_uart as _uart_detect  # noqa: E402


@mcp.tool()
def uart_detectar_dispositivo(
    puerto: str,
    baudrate: int = 115200,
    line_ending: str = "\n",
) -> dict:
    """Detecta qué dispositivo está conectado a un puerto serie.

    Conecta al puerto, envía comandos de identificación y devuelve:
    - sistema operativo (Linux, U-Boot, etc.)
    - versión de kernel
    - hostname
    - tipo de dispositivo inferido

    Usar SIEMPRE cuando el usuario pregunte "¿qué hay en COMx?"
    en lugar de solo listar puertos disponibles.

    Args:
        puerto: Nombre del puerto (ej. "COM4", "/dev/ttyUSB0").
        baudrate: Velocidad en baudios (default 115200).
        line_ending: Terminador de línea (default "\\n"; prueba "\\r\\n" si el
            dispositivo no responde).
    """
    return _uart_detect(puerto, baudrate=baudrate, line_ending=line_ending)


# ---------------------------------------------------------------------------
# Native tools (Tailscale)
# ---------------------------------------------------------------------------

from native_tools.tailscale import (  # noqa: E402, I001
    authorize_device as _ts_authorize_device,
    delete_device as _ts_delete_device,
    get_acls as _ts_get_acls,
    get_device as _ts_get_device,
    get_dns as _ts_get_dns,
    list_devices as _ts_list_devices,
)


@mcp.tool()
def tailscale_list_devices() -> list[dict]:
    """Lista todos los dispositivos del tailnet."""
    return _ts_list_devices()


@mcp.tool()
def tailscale_get_device(device_id: str) -> dict:
    """Detalles de un dispositivo por ID."""
    return _ts_get_device(device_id)


@mcp.tool()
def tailscale_get_acls() -> dict:
    """Política ACL actual (HuJSON)."""
    return _ts_get_acls()


@mcp.tool()
def tailscale_get_dns() -> dict:
    """Configuración DNS del tailnet."""
    return _ts_get_dns()


@mcp.tool()
def tailscale_authorize_device(device_id: str) -> dict:
    """Autoriza un dispositivo pendiente."""
    return _ts_authorize_device(device_id)


@mcp.tool()
def tailscale_delete_device(device_id: str) -> dict:
    """Elimina un dispositivo del tailnet."""
    return _ts_delete_device(device_id)


# ---------------------------------------------------------------------------
# Native tools (GitHub)
# ---------------------------------------------------------------------------

from native_tools.github import (  # noqa: E402, I001
    create_issue as _gh_create_issue,
    get_issue as _gh_get_issue,
    get_repo_info as _gh_get_repo_info,
    list_prs as _gh_list_prs,
    list_repos as _gh_list_repos,
)


@mcp.tool()
def github_list_repos(user: str) -> list[dict]:
    """Lista repositorios de un usuario u organización."""
    return _gh_list_repos(user)


@mcp.tool()
def github_get_repo_info(owner: str, repo: str) -> dict:
    """Información de un repositorio."""
    return _gh_get_repo_info(owner, repo)


@mcp.tool()
def github_get_issue(owner: str, repo: str, issue_number: int) -> dict:
    """Detalles de una issue."""
    return _gh_get_issue(owner, repo, issue_number)


@mcp.tool()
def github_create_issue(owner: str, repo: str, title: str, body: str = "") -> dict:
    """Crea una nueva issue."""
    return _gh_create_issue(owner, repo, title, body)


@mcp.tool()
def github_list_prs(owner: str, repo: str, state: str = "open") -> list[dict]:
    """Lista pull requests abiertos."""
    return _gh_list_prs(owner, repo, state)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Arranca el aggregator MCP vía stdio con manejo limpio de cierre."""
    log.info("Homelab Aggregator listo — escuchando stdio")
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt recibido — cerrando limpiamente")
    except Exception as e:
        log.exception("Error fatal en el aggregator: %s", e)
        raise
    finally:
        log.info("Homelab Aggregator terminado")


if __name__ == "__main__":
    main()
