# Framework — Deferrals

Esta es la lista viva de lo que el framework Mimir **no** cubre todavía.
Sirve para que cualquier sesión pueda retomar sin releer el plan entero.

Última revisión: 2026-05-02 — tras releases v0.2.0 (security hardening),
v0.3.0 (Fase 8 cleanup + keyring + scaffolder), v0.3.1 (loader credential
discovery fix) y v0.4.0 (engram HTTP backend). Versión actual en PyPI
como `mimir-router-mcp`.

## Hecho (ya no diferido)

### Foundation (rediseño modular)
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
  `credential_refs` y bloquea credenciales ajenas — Fase 6d.
- **`[runtime].command` + `args`** como alternativa a `[runtime].entry`
  — permite `uv run`, `uvx`, `node` etc. con `{plugin_dir}` como
  substitution portable. `cwd` del subprocess se fija a `manifest.path`.
- `core/loader.tool_allowed` helper + `QuarantineEntry` para
  plugin.toml malformado.
- `SqliteMemory` resuelve paths contra `router.ROOT`.
- `[requires.hosts]` soporta `tag` filter.

### Cutover y limpieza (v0.2.0 + v0.3.0)
- **Audit 2026-04-26-1242 — 9 fixes de seguridad** aplicados en v0.2.0:
  fail-closed de profiles malformados, audit log rotation, evento de
  seguridad en plugin install/remove, atomic vault write, LIKE wildcards
  escapados en `SqliteMemory.search`, duplicate-secret detection a stderr,
  `server.py` DeprecationWarning, manifest fields documentados como
  parsed-not-enforced, deps `paramiko/pygithub/requests/pyserial` movidas
  a `[legacy]` extra. Doc en `docs/security/audit-2026-04-26-1242.md`.
- **Fase 8 cleanup — completada en v0.3.0**: `server.py` y `native_tools/`
  borrados del repo. Los 4 tests legacy movidos a `tests/legacy/`
  (excluido de la suite activa por `pyproject.toml:norecursedirs`).
  Extra `[legacy]` eliminado.
- **Cutover plan preparatorio** (Fase 7a): 7 manifests reales en
  `docs/cutover/manifests/*/plugin.toml` (proxmox, linux, windows,
  docker, unifi, uart, gpon) + `docs/cutover/README.md` con los pasos
  exactos, cubiertos por `tests/test_cutover_manifests.py`.

### Features nuevas (v0.3.0 + v0.3.1)
- **Keyring MVP**: `core.secrets` resuelve credenciales vía OS keyring
  (Windows Credential Manager / macOS Keychain / secret-service Linux).
  Orden: env → keyring → vault file → .env. `router_add_credential`
  espeja al keyring tras el write al vault file. Fallback silencioso
  si el backend no está disponible.
- **`router_scaffold_plugin`**: tool MCP que genera
  `plugins/<name>/plugin.toml` skeleton desde 4 args (name, command, args,
  credential_refs). Reusa `_validate_plugin_name` y `_resolve_within`.
  No crea `server.py`.
- **Loader credential discovery via vault file** (FIXED v0.3.1):
  `_check_requirement` en `core/loader.py` ahora usa `list_candidate_refs()`
  de `core.secrets`, que agrega env + vault file + `.env`. Antes solo
  iteraba `os.environ` y los plugins con credenciales en vault quedaban
  en `pending_setup` para siempre. 2 tests regression añadidos.

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
- ✅ **Schema validation `proxmox_nodes.json`** — IMPLEMENTADO en plugin
  homelab v1.3.0 (no en framework — la validación es responsabilidad del
  plugin, ya que el JSON es schema-específico del homelab). Fail-loud al
  boot con mensaje claro. Item resuelto fuera del framework.

## Diferido — memoria

- ✅ **Engram memory adapter** — IMPLEMENTADO en v0.4.0 (2026-05-02).
  `core/memory/engram.py` delega a `engram serve` HTTP API. Sin
  dependencia adicional (urllib stdlib). Configuración en
  `router.toml [memory] backend = "engram" project = "..."`. Default
  sigue siendo `noop` (zero-config). Ver CHANGELOG v0.4.0 para detalles.
- **`claude_mem` backend**. Same path que tuvo engram: pendiente
  cuando aparezca un caso de uso concreto.

## Diferido — divergencia manifest/runtime

- **`plugins/homelab/plugin.toml` declara envvars granulares** (`PROXMOX_*_HOST`,
  `PROXMOX_*_USER`, `PROXMOX_*_TOKEN`) pero el runtime real consume un único
  archivo JSON aggregado vía `PROXMOX_NODES_FILE`. Funciona porque el glob
  `PROXMOX_*` en `credential_refs` captura `PROXMOX_NODES_FILE` por
  coincidencia léxica. Manifest actualizado el 2026-05-01 para reflejar
  ambos paths (NODES_FILE preferido). Decisión definitiva (consolidar a
  un solo path) queda diferida hasta extracción del plugin a su repo.

## Diferido — extracción y cutover

- **Fase 7b** (ejecución): añadir `plugin.toml` a cada repo upstream
  (`CTRQuko/homelab-mcp`, `CTRQuko/gpon-mcp`,
  `CTRQuko/serial-mcp-toolkit`), checkoutear/symlinkar en `plugins/` y
  validar con `router.py --dry-run`. Los manifests ya existen
  (`docs/cutover/manifests/`) y el plan paso a paso está en
  `docs/cutover/README.md`. Requiere commits a repos externos ⇒ OK
  explícito del operador.
- **Fase 7c** (nativo): ✅ RESUELTO por eliminación. `native_tools/{github,
  tailscale,uart_detect}.py` se borraron en Fase 8 cleanup (v0.3.0). El
  framework no incluye módulos OS nativos — todo lo que necesite primitives
  del SO se implementa como plugin externo. Los `core OS modules` (windows,
  linux, shell, git, python, node) ya NO están en el roadmap.
- **Fase 7d**: `homelab-mcp` agrupa 4 sub-MCPs (`homelab-{proxmox,linux,
  windows,docker}-mcp`). El manifest actual sólo monta el `proxmox` sub-MCP;
  los otros 3 son inalcanzables vía mimir aunque el código existe en el
  repo upstream. Trocear en 4 repos o 4 manifests queda para después.
  **Impacto user-facing**: capa 2 (SSH+sudo) y operación Docker quedan
  como flujos manuales en CLAUDE.md hasta que se monten los sub-MCPs.
- **Fase 9**: README del framework, push final.

## Decisiones congeladas hasta Fase 7+

- `mcp-servers/homelab-mcp/` y `mcp-servers/gpon-mcp/` no se borran —
  Hermes y Claude Desktop todavía dependen de ellos.
- Las configs de los clientes MCP no se modifican.

## Estado de cobertura

- **238 passing** en suite activa (v0.4.0 baseline). +17 tests del
  engram HTTP backend (`test_core_memory_engram.py`) sobre los 221 de
  v0.3.1. El pre-existing fail `test_github_client_anonymous_emits_warning`
  desapareció del cómputo al moverse a `tests/legacy/` con Fase 8 cleanup.
- `router.py --dry-run` arranca limpio con 0 plugins, 0 hosts.
- Audit `2026-04-26-1242` cerrado en v0.2.0 (9 fixes aplicados).
