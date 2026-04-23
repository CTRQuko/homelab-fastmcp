# Framework — Deferrals

Esta es la lista viva de lo que el rediseño modular **no** cubre todavía.
Sirve para que cualquier sesión pueda retomar sin releer el plan entero.

Última revisión: 2026-04-23 — tras commits Fase 5 (audit wrap),
Fase 6b (mount), Fase 6c (tools filter middleware).

## Hecho (ya no diferido)

- Montaje real de plugins vía `create_proxy` — `router._mount_plugin`
  (Fase 6b, commit `431f226`).
- Router arranca FastMCP con `router_*` tools expuestas
  (`build_mcp`, commit `e26ad2c` y previos).
- Meta-tools dinámicas `setup_<plugin>()` para plugins pending,
  auditadas como el resto (Fase 5).
- Profile gating: `profiles/<name>.yaml:enabled_plugins` leído en
  `RouterState.bootstrap` vía `core.profile`.
- `core/skills.py` — discovery `.md` con frontmatter para skills/agents,
  con skip de `node_modules` / `.git` / `.venv` y depth cap.
- `[tools].whitelist/blacklist` enforceado por middleware FastMCP
  (on_list_tools + on_call_tool) — Fase 6c, commit `75c3c72`.
- **Env propagation a plugin subprocess** con scoping Layer 3 entre
  plugins hermanos. `_plugin_subprocess_env` respeta
  `credential_refs` y bloquea credenciales ajenas — Fase 6d. Resuelve
  el gap que obligaba a `server.legacy.py` a hardcodear env por plugin
  (ver `docs/MCP-DOWNSTREAM-ISSUES.md`).
- `core/loader.tool_allowed` helper + `QuarantineEntry` para
  plugin.toml malformado.
- `SqliteMemory` resuelve paths contra `router.ROOT`.
- `[requires.hosts]` soporta `tag` filter.

## Diferido — runtime

- **Venv / deps management** (`[runtime].venv = "auto"` + `deps = [...]`).
  Hoy el contrato es "el plugin trae su propio entorno": `_plugin_mount_config`
  lanza el subprocess con el Python del router y el entry declarado, sin
  instalar nada. Para automatizar hace falta decidir entre `uv`, `venv
  stdlib` o detección, y dónde cachear los entornos.
- **Layer 5 tier 2** — `network_dynamic`, `filesystem_read`,
  `filesystem_write`, `exec`. Se parsean del manifest pero no se aplican.
  Enforcement real requiere un sandbox a nivel proceso (seccomp, AppArmor,
  namespace, o el propio subprocess con permisos limitados); hacerlo
  in-process sería teatro de seguridad. Scheduled junto con el runtime
  sandbox, no antes.
- **Core OS modules** — `windows`, `linux`, `shell`, `git`, `python`,
  `node`. El plan los ubica en `core/`; hoy los que existen viven en
  `native_tools/` y se usan vía `server.legacy.py`. La migración se
  destrabará cuando Fase 7 empiece a mover plugins a repos separados.

## Diferido — memoria

- Backends `engram` y `claude_mem`. Solo hay `noop` y `sqlite`. La
  interfaz `MemoryBackend` está lista — falta el adapter.

## Diferido — extracción y cutover

- **Fase 7**: crear repos `plugins-{github,tailscale,uart,proxmox,linux,
  windows,docker,gpon}-mcp`, añadir `plugin.toml` a cada uno y reescribir
  para consumir `core.inventory` en vez de referencias hardcodeadas.
- **Fase 8**: apuntar Hermes (LXC 302 pve2) y Claude Desktop a `router.py`
  en vez de `server.py`, validar que el set de tools coincide, y solo
  entonces eliminar `server.legacy.py`, `native_tools/`, `mcp-servers/
  homelab-mcp/`, `mcp-servers/gpon-mcp/`.
- **Fase 9**: README del framework, merge `refactor/modular-framework` →
  `main`, push.

## Decisiones congeladas hasta Fase 7+

- `server.py` legacy no se toca; su copia sigue en `server.legacy.py`.
- `mcp-servers/homelab-mcp/` y `mcp-servers/gpon-mcp/` no se borran —
  Hermes y Claude Desktop todavía dependen de ellos.
- Las configs de los clientes MCP no se modifican.
- Branch `refactor/modular-framework` no se pushea hasta Fase 9.

## Estado de cobertura

- 249 passed + 2 skipped (baseline de este rediseño era 112).
- `router.py --dry-run` arranca limpio con 0 plugins, 0 hosts.
- Security review pasado con 4 fixes (newline injection, disabled-plugin
  widening, tomllib guard, `.md` col-0 strict parse) + Layer 5 tier 1
  wireado.
