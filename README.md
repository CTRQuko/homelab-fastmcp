# homelab-fastmcp

MCP Aggregator en Python con FastMCP 3.x. Monta downstream MCPs + tools nativas.

## Caracteristicas

- **Cross-platform**: Windows, Linux, macOS (via `HOMELAB_DIR`)
- **Adaptativo**: Omite `windows`/`docker` en Linux/macOS automaticamente
- **Seguro**: Input validation regex, sanitizacion de errores, secrets externalizados
- **Testeado**: 95+ tests (seguridad, integracion, resiliencia, adaptacion, contratos)
- **Logging**: arranque estructurado en stderr (stdout reservado al protocolo MCP)

## Estructura

```
native_tools/          # Tools nativas en Python
  secrets.py           # Loader unificado de secrets (cross-platform)
  github.py            # API REST GitHub con validacion
  tailscale.py         # API REST Tailscale con sanitizacion
  uart_detect.py       # Deteccion automatica de dispositivos serie

server.py              # Aggregator FastMCP (adaptativo por plataforma)

tests/                 # 95+ tests pytest
  test_integration.py       # exposicion de tools
  test_security.py          # validacion inputs
  test_security_extended.py # sanitizacion + masking
  test_resilience.py        # comportamiento ante fallos
  test_adaptive.py          # cross-platform
  test_critical.py          # contratos fundamentales + regresiones
  test_coverage_gaps.py     # areas peor cubiertas
  manual/                   # tests con hardware real (excluidos de pytest normal)
```

## Quick Start

### Windows

```powershell
# 1. Clonar en C:\homelab\laboratorio\homelab-fastmcp (o ajustar HOMELAB_DIR)
# 2. Configurar secrets en C:\homelab\.config\secrets\*.md
# 3. Instalar dependencias
uv sync --extra test
# 4. Verificar tests
uv run --extra test pytest tests/ -v    # 95 passed, 2 skipped
# 5. Arranque manual (opcional, para smoke test)
uv run homelab-fastmcp                   # queda escuchando stdio
```

### Linux / macOS

```bash
export HOMELAB_DIR=/home/tuusuario/homelab
uv sync --extra test
uv run --extra test pytest tests/ -v
uv run homelab-fastmcp
```

## Uso como MCP server

`homelab-fastmcp` se registra como subproceso stdio en cualquier cliente MCP.
Ejemplos:

### OpenCode (`~/.config/opencode/opencode.json`)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "homelab-fastmcp": {
      "command": [
        "uv", "run", "--directory",
        "C:\\homelab\\laboratorio\\homelab-fastmcp",
        "homelab-fastmcp"
      ],
      "enabled": true,
      "type": "local"
    }
  }
}
```

### Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "homelab-fastmcp": {
      "command": "uv",
      "args": [
        "run", "--directory",
        "C:\\homelab\\laboratorio\\homelab-fastmcp",
        "homelab-fastmcp"
      ]
    }
  }
}
```

En Linux/macOS sustituir la ruta por la del clonado (ej. `~/homelab/laboratorio/homelab-fastmcp`).

## Configuracion

### Variables de entorno

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `HOMELAB_DIR` | `C:/homelab` (Win) | Directorio raiz del homelab |
| `HOMELAB_LOG_LEVEL` | `INFO` | Nivel de logging (DEBUG/INFO/WARNING/ERROR) |
| `HOMELAB_DOTENV_WINS` | `0` | `1` para que el `.env` del proyecto pise env vars externas |
| `UNIFI_API_KEY` | — | API key de UniFi |
| `UNIFI_API_TYPE` | `local` | `local`, `cloud-ea`, `cloud-v1` |
| `TAILSCALE_API_KEY` | — | API key de Tailscale |
| `TAILSCALE_TAILNET` | — | Nombre del tailnet |
| `GITHUB_TOKEN` | — | Token de GitHub (opcional, sin token: anonymous 60 req/h) |

### Prioridad de secrets

1. Variable de entorno
2. `$HOMELAB_DIR/.config/secrets/*.md`
3. `.env` en raiz del proyecto

> **Credenciales stale en el entorno del sistema**
>
> Si una env var externa (p. ej. Windows User Environment, launcher, shell)
> exporta un valor viejo/revocado de una credencial, tiene prioridad sobre
> el `.env` local. El aggregator emite un `WARNING [homelab-fastmcp] external
> env differs from .env for: [KEYS]` en stderr al arrancar si detecta esta
> disparidad en credenciales críticas (UNIFI_*, GPON_*, PROXMOX_*, TAILSCALE_*,
> GITHUB_*).
>
> Opciones:
> - **Limpiar la env var externa** (recomendado). En Windows:
>   `[Environment]::SetEnvironmentVariable('UNIFI_API_KEY', $null, 'User')`
> - **Forzar `.env` como source of truth** exportando `HOMELAB_DOTENV_WINS=1`
>   antes de lanzar el cliente MCP.
>
> **Nota sobre reinicios:** el protocolo MCP stdio congela el `os.environ`
> del subprocess al lanzar. Tras cambiar credenciales, reiniciar el cliente
> MCP (Claude Desktop, OpenCode, …) para que las nuevas sean vistas.

## Downstreams

| Namespace | Plataforma | Descripcion |
|-----------|-----------|-------------|
| `windows_*` | Windows-only | PowerShell, filesystem |
| `linux_*` | Todas | SSH a hosts Linux |
| `proxmox_*` | Todas | VMs, LXCs, nodos Proxmox |
| `docker_*` | Windows-only | Contenedores e imagenes |
| `unifi_*` | Todas | Red UniFi, VLANs, clientes |
| `uart_*` | Todas | UART/Serie generico |
| `gpon_*` | Todas | Sticks GPON via SSH |

## Tests

```bash
# Toda la suite (excluye tests/manual/)
uv run --extra test pytest tests/ -v              # 95 passed, 2 skipped

# Solo unit + critical + coverage (sin integracion, como en CI)
uv run --extra test pytest tests/ -m "not integration" -v

# Por categoria
uv run --extra test pytest tests/test_security*.py -v
uv run --extra test pytest tests/test_critical.py -v
uv run --extra test pytest tests/test_coverage_gaps.py -v

# Tests manuales (requieren hardware)
python tests/manual/test_hardware.py
```

## Estado

- **Version**: 0.3.1
- **9/9** servicios funcionan (funcional)
- **95/95** tests pasan (2 skipped: Linux-only en Windows + servers.json ausente)
- **0** secrets hardcodeados en codigo fuente
- **Ruff**: clean (`uvx ruff check .`)
- **CI**: GitHub Actions matrix (ubuntu + windows)

## Documentacion

- [`docs/INSTALL.md`](docs/INSTALL.md) — Guia de instalacion detallada
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Decisiones de arquitectura
- [`docs/SECURITY.md`](docs/SECURITY.md) — Modelo de seguridad
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — Historial de cambios
