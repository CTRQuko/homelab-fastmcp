"""Homelab FastMCP Aggregator.

Reemplaza al mcp-router Go con FastMCP Python.
Monta downstream MCPs con namespacing automático.
"""
import logging
import os
import sys
from datetime import datetime
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
# Failure log file (append mode)
# ---------------------------------------------------------------------------
_FAILURE_LOG = Path(__file__).resolve().parent / "failure.log"


def log_failure(namespace: str, tool: str, error: str) -> None:
    """Append failure to failure.log."""
    try:
        with open(_FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {namespace}.{tool} | {error}\n")
    except Exception:
        pass  # Never fail due to logging failures

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
# HOMELAB_DOTENV_WINS=1 (env var EXTERNA) hace que .env del proyecto pise
# siempre a las env vars externas. Útil cuando el parent process exporta
# credenciales viejas/stale que no se pueden limpiar (env var user Windows,
# launcher corporate, etc.). Default: convención dotenv estándar (externa gana).
_dotenv_wins = os.environ.get("HOMELAB_DOTENV_WINS", "0").strip() == "1"

_env_file = Path(__file__).resolve().parent / ".env"
_env_lines: list[str] = []
if _env_file.exists():
    try:
        _env_lines = _env_file.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError) as _e:
        # .env ilegible (bytes no-UTF-8, permisos, etc.) NO debe tumbar el
        # aggregator. Log y continuar con env vars externas solamente.
        log.warning(
            ".env no se pudo leer (%s): %s. Continuando con env vars externas.",
            type(_e).__name__, _e,
        )

if _env_lines:
    _overrides_logged = []
    for _line in _env_lines:
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _parse_env_value(_val)
            _prev = os.environ.get(_key)
            if _dotenv_wins:
                # .env siempre gana
                if _prev and _prev != _val:
                    _overrides_logged.append(_key)
                os.environ[_key] = _val
            else:
                # Estándar dotenv: externa gana si tiene valor
                if not _prev:
                    os.environ[_key] = _val
                elif _prev != _val and _key.startswith(
                    ("UNIFI_", "GPON_", "PROXMOX_", "TAILSCALE_", "GITHUB_")
                ):
                    # Warning early: env externa difiere del .env en una credencial.
                    # Posible env stale (ver commit 75a35d0 para el caso UniFi).
                    _overrides_logged.append(_key)
    if _overrides_logged and not _dotenv_wins:
        print(
            f"WARNING [homelab-fastmcp] external env differs from .env for: "
            f"{sorted(_overrides_logged)}. External wins (dotenv convention). "
            f"Set HOMELAB_DOTENV_WINS=1 to force .env. "
            f"Restart client MCP if credentials seem stale.",
            file=sys.stderr,
        )
    elif _overrides_logged and _dotenv_wins:
        print(
            f"INFO [homelab-fastmcp] .env overrode external env for: "
            f"{sorted(_overrides_logged)} (HOMELAB_DOTENV_WINS=1)",
            file=sys.stderr,
        )

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
os.environ.setdefault("UNIFI_API_TYPE", "local")
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

# ---------------------------------------------------------------------------
# Subprocess env builder
# ---------------------------------------------------------------------------
# Prefixes that identify "domain" env vars. Each downstream only sees its own
# domain's vars — not those of other domains. Non-domain vars (PATH, APPDATA,
# USERPROFILE, HOME, …) ARE inherited so uv/uvx can find Python, caches and
# user dirs when the aggregator is launched from a client that doesn't pass
# a full environment to the MCP subprocess.
_DOMAIN_PREFIXES = ("UNIFI_", "GPON_", "PROXMOX_", "TAILSCALE_", "GITHUB_")


def _build_subprocess_env(own_prefix: str) -> dict:
    """Build env dict for a downstream subprocess.

    - Inherits all os.environ entries with non-empty values.
    - Excludes vars from OTHER domain prefixes (no cross-contamination).
    - The downstream's own prefix is kept (that's the point).
    """
    other_prefixes = tuple(p for p in _DOMAIN_PREFIXES if p != own_prefix)
    return {
        k: v for k, v in os.environ.items()
        if v and not k.startswith(other_prefixes)
    }

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
            ],
            "env": {
                "PROXMOX_NODES_FILE": f"{HOMELAB_DIR}/mcp-servers/homelab-mcp/proxmox_nodes.json"
            },
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

_unifi_env = _build_subprocess_env("UNIFI_")
_unifi_config = {
    "mcpServers": {
        "default": {
            "command": "uvx",
            "args": ["unifi-mcp-server"],
            # Full os.environ base (minus other domains) + UNIFI_* overrides.
            # create_proxy may replace the subprocess env wholesale; without PATH,
            # APPDATA, USERPROFILE, uvx cannot locate its cache on Windows.
            "env": _unifi_env,
        }
    }
}
mcp.mount(create_proxy(_unifi_config), namespace="unifi")
_unifi_domain_keys = sorted(k for k in _unifi_env if k.startswith("UNIFI_"))
log.info("Downstream montado: unifi (UNIFI_* propagados: %s)", _unifi_domain_keys)
if not _unifi_domain_keys:
    log.warning("unifi: ninguna variable UNIFI_* con valor — downstream puede fallar")

if log.isEnabledFor(logging.DEBUG):
    from native_tools.secrets import mask as _mask  # lazy import
    for _k in _unifi_domain_keys:
        log.debug("unifi env %s=%s", _k, _mask(_unifi_env[_k]))

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

_gpon_env = _build_subprocess_env("GPON_")
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
            # Same pattern as unifi: full base env minus other domains + GPON_*.
            "env": _gpon_env,
        }
    }
}
mcp.mount(create_proxy(_gpon_config), namespace="gpon")
_gpon_domain_keys = sorted(k for k in _gpon_env if k.startswith("GPON_"))
log.info("Downstream montado: gpon (GPON_* propagados: %s)", _gpon_domain_keys)
if log.isEnabledFor(logging.DEBUG):
    from native_tools.secrets import mask as _mask  # lazy import
    for _k in _gpon_domain_keys:
        log.debug("gpon env %s=%s", _k, _mask(_gpon_env[_k]))

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
