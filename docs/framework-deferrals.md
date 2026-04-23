# Framework MVP — Deferrals

Esta sesión entregó el esqueleto mínimo del rediseño modular de
`homelab-fastmcp`. Lo siguiente queda **explícitamente diferido** a sesiones
posteriores para mantener el cambio pequeño y no romper a Hermes ni a
Claude Desktop.

## Diferido — arquitectura

- **Montaje real de plugins** (`[runtime]` entry, `deps`, `venv=auto`).
  `router.py` descubre y reporta estado, pero no arranca procesos ni importa
  módulos. El mount sigue en `server.py` legacy.
- **Integración MCP** del router. Las tools `router_*` existen como
  funciones puras (`core/bootstrap.py`) pero aún no se exponen vía
  `@mcp.tool()`.
- **Meta-tools `setup_<plugin>()`** dinámicas anunciadas por
  `router_help()`. Falta el registrador en la Fase 4.
- **Backends de memoria `engram` y `claude_mem`**. Solo hay `noop` y
  `sqlite`.
- **Profile gating**. `profiles/default.yaml` existe pero nadie lee
  `enabled_plugins` todavía.
- **Interceptores runtime** (red / FS / exec) — Fase 5 del plan.
- **Core OS modules** `windows`, `linux`, `shell`, `git`, `python`, `node`.
- **Extracción de plugins existentes** a repos `plugins-*-mcp` separados.

## Diferido — código

- `[tools].whitelist/blacklist` y `[security].filesystem_*/exec` del
  manifest se parsean (`core/loader.parse_manifest`) pero ningún consumer
  los aplica. Reservados para Fase 5.
- `LoadReport.to_dict()` no serializa `manifest.path`.
- `SqliteMemory` por defecto usa path relativo a CWD — debería
  resolverse contra `router.ROOT` en Fase 4.
- `[requires.hosts]` no soporta filtrado por `tag`. La requirement schema
  lo ignora silenciosamente.
- `discover_manifests` aborta la iteración completa si **un** plugin tiene
  `plugin.toml` malformado. Falta cuarentena por plugin.

## Decisiones congeladas durante el MVP

- `server.py` **no se toca**. Su copia queda en `server.legacy.py`.
- Los repos `mcp-servers/homelab-mcp/` y `mcp-servers/gpon-mcp/` **no se
  borran** — Hermes sigue dependiendo de ellos hasta Fase 7/8.
- Las configs de los clientes MCP (Claude Desktop, Hermes) **no se modifican**.
- No se crean repos separados (`gh repo create`) en esta sesión.

## Estado de cobertura

- 167 tests passed + 2 skipped tras los fixes (baseline era 112).
- `router.py --dry-run` arranca limpio sin inventory ni plugins.
- Security review pasado con fixes aplicados (newline injection,
  disabled-plugin allowlist, tomllib error handling, `KEY=` strict parse).
