# Changelog

Todas las novedades relevantes de `homelab-fastmcp`.

El formato sigue [Keep a Changelog](https://keepachangelog.com/) y usa
[Semantic Versioning](https://semver.org/).

## [0.3.2] — 2026-04-22

### Fixed
- **secrets**: `_from_dotenv()` ahora usa `_parse_env_value()` — paridad con
  `server._parse_env_value()` (fix R2/R5). Antes: parser primitivo que no
  manejaba comillas anidadas ni `#` dentro de valores entre comillas.
- **server**: `HOMELAB_DIR` ahora tiene fallback `/home` en Linux/macOS
  (fix R8). Antes: hardcoded `C:/homelab` — fallaba silenciosamente
  en otras plataformas.
- **uart_detect**: `import re` movido a nivel módulo (fix R10). Antes:
  inline `import re as _re` dentro de función — antipatrón de linter.

### Added
- `docs/BUGS.md` — tabla de bugs con códigos R#, estado y fix aplicado.

## [0.3.1] — 2026-04-22

### Fixed
- **build**: `[project.scripts] homelab-fastmcp = "server:main"` fallaba con
  `ModuleNotFoundError: No module named 'server'` al ejecutarse vía
  `uv run homelab-fastmcp`. Causa: `[tool.setuptools.packages.find]
  include=["server*"]` trataba `server` como glob de paquete, no como módulo
  single-file. Fix: declarado explícitamente con
  `[tool.setuptools] py-modules = ["server"]`.
  Esto permite que los clientes MCP (OpenCode, Claude Desktop) invoquen el
  aggregator con `uv run --directory <path> homelab-fastmcp`.
- **ci**: `test_linux_always_available` y `test_windows_only_on_windows`
  fallaban en CI porque requieren los downstream MCPs instalados
  (`$HOMELAB_DIR/mcp-servers/homelab-mcp`). Marcados con
  `@pytest.mark.integration`; CI los excluye con `-m "not integration"`.

### Added
- Ejemplos de configuración para OpenCode (`opencode.json`) y
  Claude Desktop (`claude_desktop_config.json`) en la documentación.

## [0.3.0] — 2026-04-22

### Added
- `native_tools/uart_detect.py` — detección automática de dispositivos en puertos serie
- `native_tools/tailscale.py` — cliente REST con sanitización de credenciales en errores
- `native_tools/github.py` — wrapper PyGithub con validación estricta de nombres
- `native_tools/secrets.py` — loader con prioridad `env > secrets/*.md > .env`
- `tests/test_critical.py` — 25 tests de contratos fundamentales + regresión
- `tests/test_coverage_gaps.py` — 12 tests sobre áreas peor cubiertas
- `tests/manual/` — scripts de test con hardware real (excluidos del pytest normal)
- Logging estructurado en `server.py` (arranque, mounts, warnings)
- Manejo limpio de `KeyboardInterrupt` en `main()`
- `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/CHANGELOG.md`

### Changed
- `_parse_env_value()` extraída como helper público en `server.py`:
  maneja correctamente comentarios inline y comillas (fix R2)
- `UNIFI_*` y `GPON_*` vacías ya **no se propagan** al downstream (fix R4,
  regresión real del bug UniFi 401)
- `_DEVICE_ID_RE` limita longitud a 64 caracteres (defensa DoS, fix R3)
- `_GH_NAME_RE` rechaza nombres empezando por `.` o `-` (fix R21)
- `github._client()` emite `UserWarning` al caer a modo anónimo (fix R16)
- `uart_detect`: `_settle` proporcional a `timeout_cmd` (fix R23 — antes 5s+ fijos)
- `uart_detect`: nuevo parámetro `line_ending` para compat con dispositivos CR+LF
- `secrets._from_md_files` avisa si detecta claves duplicadas (fix R17)

### Removed
- `native_tools/gpon_native.py` — código muerto con credenciales hardcoded
- `conversacion.md` — transcript con secrets en claro
- `downstream/servers.json` — artefacto documental obsoleto
- `server.py.bak.*` (4 archivos) — backups sin control de versiones
- `pdm.toml`, `pdm.lock` — residuales (el proyecto usa `uv`)
- Scripts de test en raíz movidos a `tests/manual/`

### Security
- Eliminados 3 API keys expuestos en `conversacion.md` (nunca commiteados)
- `.gitignore` endurecido: añade `conversacion*.md`, `*.bak*`, `pdm.lock`
- Fixture de test sanitizado: dummy key en lugar de prefijo de key real
- Validación defensiva de longitudes y caracteres en inputs de API

## [0.2.0] — 2026-04-20

### Added
- Downstream `linux` (vía homelab-linux-mcp)
- Downstream `proxmox` (vía homelab-proxmox-mcp)

## [0.1.0] — 2026-04-18

### Added
- Primera versión del aggregator FastMCP
- Downstream `windows` (vía homelab-windows-mcp)
- Reemplaza progresivamente al binario Go `mcp-router`
