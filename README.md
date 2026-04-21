# homelab-fastmcp

MCP Aggregator en Python con FastMCP 3.x. Monta downstream MCPs + tools nativas.

## Caracteristicas

- **Cross-platform**: Windows, Linux, macOS (via `HOMELAB_DIR`)
- **Adaptativo**: Omite `windows`/`docker` en Linux/macOS automaticamente
- **Seguro**: Input validation regex, sanitizacion de errores, secrets externalizados
- **Testeado**: 90+ tests (seguridad, integracion, resiliencia, adaptacion, contratos)
- **Logging**: arranque estructurado en stderr (stdout reservado al protocolo MCP)

## Estructura

```
native_tools/          # Tools nativas en Python
  secrets.py           # Loader unificado de secrets (cross-platform)
  github.py            # API REST GitHub con validacion
  tailscale.py         # API REST Tailscale con sanitizacion
  uart_detect.py       # Deteccion automatica de dispositivos serie

server.py              # Aggregator FastMCP (adaptativo por plataforma)

tests/                 # 90+ tests pytest
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
# 3. Ejecutar
uv run --extra test pytest tests/ -v    # 90 passed, 1 skipped
```

### Linux / macOS

```bash
export HOMELAB_DIR=/home/tuusuario/homelab
uv run --extra test pytest tests/ -v
```

## Configuracion

### Variables de entorno

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `HOMELAB_DIR` | `C:/homelab` (Win) | Directorio raiz del homelab |
| `HOMELAB_LOG_LEVEL` | `INFO` | Nivel de logging (DEBUG/INFO/WARNING/ERROR) |
| `UNIFI_API_KEY` | — | API key de UniFi |
| `UNIFI_API_TYPE` | `local` | `local`, `cloud-ea`, `cloud-v1` |
| `TAILSCALE_API_KEY` | — | API key de Tailscale |
| `TAILSCALE_TAILNET` | — | Nombre del tailnet |
| `GITHUB_TOKEN` | — | Token de GitHub (opcional, sin token: anonymous 60 req/h) |

### Prioridad de secrets

1. Variable de entorno
2. `$HOMELAB_DIR/.config/secrets/*.md`
3. `.env` en raiz del proyecto

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
uv run --extra test pytest tests/ -v              # 90 passed, 1 skipped

# Por categoria
uv run --extra test pytest tests/test_security*.py -v
uv run --extra test pytest tests/test_critical.py -v
uv run --extra test pytest tests/test_coverage_gaps.py -v

# Tests manuales (requieren hardware)
python tests/manual/test_hardware.py
```

## Estado

- **Version**: 0.3.0
- **9/9** servicios funcionan (funcional)
- **90/90** tests pasan (1 skipped Linux-only en Windows)
- **0** secrets hardcodeados en codigo fuente
- **Ruff**: clean (`uvx ruff check .`)

## Documentacion

- [`docs/INSTALL.md`](docs/INSTALL.md) — Guia de instalacion detallada
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Decisiones de arquitectura
- [`docs/SECURITY.md`](docs/SECURITY.md) — Modelo de seguridad
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — Historial de cambios
