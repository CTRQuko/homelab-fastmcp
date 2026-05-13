# Changelog

All notable changes to Mimir will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] — 2026-05-13 — UTF-8 fix + bootstrap validation + helpers

### Added

- **`core.secrets.get_github_token()`** helper compartido — patrón
  consistente para plugins que necesitan token GitHub. Conventional
  precedence: `GITHUB_TOKEN` env → keyring → vault → `.env`. Reusa
  `core.secrets.get_secret` por debajo. Commit `dc0ec14`.
- **`scripts/audit_to_runtime_issues.py --state-file`** flag —
  ejecución idempotente para cron / Stop hook. Persiste el timestamp
  del último error procesado en un fichero de estado; ejecuciones
  siguientes procesan sólo entradas posteriores. Commit `6425df5`.
- **Hook Stop CC automatic audit-bridge** patrón documentado en
  `docs/operator-notes/audit-bridge.md`. Cada cierre de Claude Code
  dispara el script con `--state-file`; errores de `audit.log`
  fluyen a `runtime-issues.md` sin intervención manual. Cron PS1
  equivalente para clientes sin hooks (OpenCode). Commit `bf809e3`.

### Fixed

- **`router.py` reconfigure stdout/stderr a UTF-8 on Windows** — CI
  Windows-latest fallaba con `UnicodeEncodeError: 'charmap' codec
  can't encode character` cuando `format_report()` imprimía los
  `prompt` de `[[requires.credentials]]` de plugins (caracteres
  unicode tipográficos como `→`, `—`). Reconfigure
  `sys.stdout`/`sys.stderr` a UTF-8 con `errors='replace'` al inicio
  del módulo. No-op en POSIX. Commit `56fbefe`. Cierra CI run
  #25635825402.
- **`core.bootstrap.router_add_host` valida `type`** contra
  `_VALID_HOST_TYPES` ANTES de persistir a `hosts.yaml`. Sin esto,
  un host con type inválido pasaba el bootstrap pero rompía el
  siguiente boot del router (el loader rechazaba el yaml entero).
  Repro: 2026-05-06 host con `type='ubiquiti-switch'` quedó
  persistido sin validar y mimir falló al reiniciar. Error message
  ahora lista los types válidos + sugiere usar `network-device` con
  tag específico. Commit `47e0971`.
- **`router.py` audit log error wiring** — cleanup del enrichment
  v0.5.0: `error_message` y status type llegan correctamente al
  audit log al cazar excepciones. Commit `60da3f1`.

### Changed

- **Profile `default.yaml`** habilita plugin `nginx-ui-ops` por
  defecto. Commit `619fa0f`.

### Docs

- `docs/operator-notes/spec-net-tools-plugin-20260510.md` — spec
  funcional del plugin agregado `net-tools` (Cloudflare DNS +
  AdGuard; Pi-hole descartado del scope 2026-05-13).
- `docs/operator-notes/tool-gaps-reverse-proxy-plan-20260510.md` —
  audit de gaps en inventario de tools homelab vs trabajo realmente
  necesario.
- `docs/operator-notes/audit-bridge.md` — flujo automatizado
  `audit.log` → `runtime-issues.md` con 3 modos (manual one-shot,
  Stop hook CC, cron OpenCode).
- `docs/operator-notes/runtime-issues.md` — entradas de incidentes
  operativos 2026-05-04 a 2026-05-13 + nuevo patrón recurrente
  documentado: "Manifest `[security].allow_mutations` no activa
  nada" (mimir-mcp core NO propaga ese flag como env var; el path
  real es `router_add_credential` + restart, requiere declarar el
  ref en `credential_refs`).

### Repo hygiene

- Tags git retrospectivos creados para v0.2.0 (`cbf4d28`), v0.4.0
  (`652c33e`) y v0.5.0 (`38642bc`) — releases publicados en PyPI sin
  tag git en su momento; ahora alineados.
- Tag huérfano `v0.3.2` (apuntaba a `247415d`, proyecto pre-Mimir
  `homelab-fastmcp` con `native_tools/`, `server.py` legacy) eliminado
  para limpiar el linaje. El commit permanece en git history.

### Plugins (in-tree, NO son core de mimir)

Para referencia del monorepo — estos cambios NO afectan al core de
mimir pero viven en `plugins/`:
- `plugins/unifi/` — Fase 7 cutover materializado (wrapper sobre
  `uvx unifi-mcp-server`, modo local API key). Commit `0250aa4`.
- `plugins/net-tools/` v0.1.0 — Cloudflare DNS (5 tools) + AdGuard
  multi-instance (6 tools) + resolver `multi_instance.py` reusable.
  70 tests offline (pytest-httpx mocks). Commits `4a13e91`, `3b71c4e`.

## [0.5.0] — 2026-05-06 — audit log enrichment + runtime-issues bridge

### Added

- **`core.audit` enrichment** — entries con `status != "ok"` ahora
  incluyen `error_message` (truncado 500 chars), `client` (identifica
  el cliente MCP upstream vía env var `MIMIR_CLIENT_ID` → process tree
  con psutil opcional → "unknown") y `args_sanitized` (copia de los
  args con keys secret-shaped reemplazadas por `<redacted>` y strings
  >200 chars truncados). Las entries OK siguen con `args_hash`
  únicamente — el coste extra solo aplica cuando hay algo que
  diagnosticar.

  Patrones de redaction (case-insensitive substring): `TOKEN`, `SECRET`,
  `PASSWORD`, `PASSWD`, `AUTH`, `BEARER`, `CREDENTIAL`, `COOKIE`,
  `SESSION`, `PRIVATE`, `API_KEY`, `APIKEY`, `ACCESS_KEY`, `SECRET_KEY`,
  `PRIVATE_KEY`. `KEY` solo está intencionalmente excluido para evitar
  false positives en `ok_key`/`monkey`/etc.

  `log_tool_call` añade dos kwargs nuevos (`error_message=`, `client=`)
  ambos opcionales — backwards compatible con callers existentes.

- **`scripts/audit_to_runtime_issues.py`** — bridge stdlib-only que
  extrae errores del audit log y genera *skeleton entries* en
  `docs/operator-notes/runtime-issues.md` para que el operador
  complete causa/fix/prevención. Soporta `--since "<N> hours ago"`,
  ISO datetime, unix timestamp; `--since-session-start` (psutil
  opcional) para integrar con hook `Stop` de Claude Code; `--dry-run`
  para inspección. Append-only con backup `.bak` por seguridad.

  Cross-LLM: cualquier cliente que invoque mimir pasa por audit.log
  (Claude Code, OpenCode, futuros), así que el script captura los
  errores de todos sin que el operador escriba a mano cada incidencia.

  El operador añade el hook `Stop` a `~/.claude/settings.json` para
  ejecutar el script automáticamente al cerrar sesión Claude Code.
  Para OpenCode (sin hooks nativos) → ejecutar manualmente. Cron job
  diferido a v0.6.0.

### Tests

- 15 tests nuevos en `test_core_audit.py` (9 → 24): client field,
  error_message truncation, args_sanitized in errors only, sanitization
  rules (redaction, truncation, nested structures, primitives, custom
  objects), `_resolve_client_id` con env var / fallback.
- 23 tests nuevos en `test_audit_to_runtime.py`: parsing de --since en
  todas las formas, filtrado por status/timestamp, robustez frente a
  malformed lines, agrupación por (plugin, tool, error_message[:200]),
  rendering de skeletons, append + backup, dry-run, integración CLI.

Suite total: **270 → 308 passing**.

### Documentation

- `docs/operator-notes/runtime-issues.md` cabecera reescrita: explica
  cómo se alimenta el archivo (manual + auto), cómo configurar el hook
  Stop, cómo usar el script para OpenCode.
- Entry 2026-05-04 (`homelab_ssh_run timeout hacia todos los nodos`)
  cerrada documentalmente: el timeout 30s es del plugin homelab
  v1.4.0 (`proxmox_mcp/server.py:703`), sobreescribible vía argumento
  `timeout=N`. OpenCode no impone timeout MCP. Que falle a TODOS los
  nodos sugiere problema sistémico no del timeout.

## [0.4.0] — 2026-05-02 — engram memory backend

### Added

- **`core.memory.engram.EngramMemory`** — backend que delega a un
  `engram serve` local vía HTTP API (default `http://127.0.0.1:7437`).
  Implementa los 5 métodos del `MemoryBackend` ABC (save, search, get,
  update, delete) llamando a los endpoints REST de engram. Todo con
  `urllib.request` (stdlib) — no añade ninguna dependencia.

  Configuración en `router.toml`::

      [memory]
      backend = "engram"
      project = "homelab"           # opcional pero recomendado — scopes
                                    # las saves y permite que engram cree
                                    # la session_id automáticamente
      base_url = "http://127.0.0.1:7437"   # opcional, default mostrado
      session_id = "mimir-router"          # opcional, default mostrado
      timeout = 5                           # opcional, segundos

  Comportamiento:
  - **Init fail-loud**: si engram no responde a `/health`, el constructor
    lanza `RuntimeError` con mensaje accionable. El operador ve el error
    en `router_status` y puede arrancar `engram serve` o cambiar a
    `noop`/`sqlite`.
  - **Session auto-create**: si `project` está set, hace `POST /sessions`
    al boot (engram lo trata como upsert). Si no, skipea con warning —
    el primer save fallará con FK error claro y el operador sabrá que
    debe añadir project.
  - **Errores de tool propagados**: HTTPError y URLError durante save/
    search/get/update/delete se convierten en `RuntimeError` con detalle
    para no silenciar problemas.

- **`load_backend()`** ahora reconoce `name == "engram"` y instancia el
  adapter con la config dada.

### Tests

- **16 tests nuevos** en `tests/test_core_memory_engram.py`: init health
  check (3 paths), save (4 cases con mock), search (2), get/update/
  delete (3), errores HTTP/URL durante operaciones (2), factory (2).
  Todos mockean `urllib.request.urlopen` para no tocar el engram local.
- **`test_load_backend_unknown_raises`** actualizado: usaba "engram"
  como nombre desconocido, ahora usa "does-not-exist".
- **Suite total: 238 passing** (era 221 + 1 pre-existing fail).

### Diseño

Engram es la herramienta de memoria persistente que el operador ya usa
desde Claude Code via MCP. Este adapter permite que **plugins de mimir**
también deleguen su memoria a engram en lugar de re-implementar storage
o usar sólo el sqlite local. Un plugin que quiera persistir state
cross-sesión ahora puede pedir el backend al router (vía la
infraestructura existente) y obtener una memoria centralizada que el
operador ya cura.

No-breaking: backends existentes (`noop`, `sqlite`) inalterados.

## [0.3.1] — 2026-04-27 — credential discovery bugfix

### Fixed

- **`_check_requirement` now scans vault file for glob patterns.**
  The credential requirement check in `core/loader.py` was iterating
  `os.environ` to discover matching keys for glob patterns (e.g.
  `PROXMOX_*`). Credentials written via `router_add_credential` live in
  `<MIMIR_HOME>/secrets/*.md` (vault file) and the OS keyring but are not
  injected into the router process environment. The loop was replaced with
  `list_candidate_refs()` from `core.secrets`, which aggregates
  `os.environ` + vault file + `.env`. As a result, plugins with vault-only
  credentials now correctly transition from `pending_setup` to `ok` after a
  restart, without requiring any changes to the MCP client config.

  Keyring-only refs remain non-discoverable by glob (the OS keyring API
  offers no enumeration); literal patterns still probe all sources including
  the keyring. In practice this limitation does not apply because
  `router_add_credential` always writes to both the vault file and the
  keyring.

### Tests

- Two new regression tests in `tests/test_core_loader.py`:
  `test_credential_requirement_glob_matches_vault_not_env` and
  `test_credential_requirement_glob_absent_from_all_sources`.
  Active suite: **221 passed**.

## [0.3.0] — 2026-04-26 — Fase 8 cleanup + keyring (MVP) + plugin scaffolder

This release closes the Fase 8 cleanup promised in v0.2.0 and adds two
new capabilities: OS keyring as a credential source, and a tool to
scaffold new plugin manifests from MCP.

> **Renamed on PyPI: `mimir-mcp` → `mimir-router-mcp`.** The original
> name was claimed (without releases) by another account before this
> project's first publish. The framework, the CLI command (`mimir`),
> the importable Python module (`router`), the GitHub repo
> (`CTRQuko/mimir-mcp`) and the brand all stay as "Mimir" — only the
> string used by `pip` / `uv` to fetch the wheel changes. From v0.3.0
> onwards, install with `pip install mimir-router-mcp` /
> `uv add mimir-router-mcp`.

### Added

- **OS keyring as credential source.** `core.secrets` now resolves
  credentials via the OS keyring (Windows Credential Manager / macOS
  Keychain / secret-service on Linux) under service name `mimir`.
  Resolution order is **env → keyring → vault file → .env**. The
  `keyring` package is imported lazily, and any failure (missing
  backend, headless session, broken installation) falls through
  silently to the next source. Operators in unattended environments
  see no behaviour change.
- **`router_add_credential` mirrors writes to keyring.** After the
  vault file write, the router also calls `keyring.set_password` for
  the same ref/value. Returned dict gains a `keyring: bool` field so
  callers can see whether the mirror landed. Failure is non-fatal;
  the vault file remains the authoritative copy on disk.
- **`router_scaffold_plugin(name, command, args, credential_refs,
  description)` MCP tool.** Generates `plugins/<name>/plugin.toml`
  from arguments. Refuses to overwrite an existing directory. Reuses
  the same name validation and path-resolution defences as
  `router_install_plugin`. **Does not** create `server.py` — that is
  the plugin's responsibility (or the upstream MCP repo's). After
  scaffolding, restart Mimir to discover the new plugin. See
  `docs/plugin-contract.md` § "Scaffolding a new plugin".

### Removed

- **`server.py` and `native_tools/`** (the pre-rename aggregator)
  deleted from the source tree. The four tests that imported from
  them moved to `tests/legacy/` (already excluded from the active
  suite by pyproject.toml `norecursedirs`).
- **`[project.optional-dependencies].legacy` extra removed**:
  `paramiko`, `pygithub`, `requests`, `pyserial` had no consumer
  in the framework anymore. `uv.lock` shrinks accordingly.
- **Transitional comments and `exclude = ["native_tools*"]`** in
  `[tool.setuptools.packages.find]` — no longer needed once the
  files are gone.

### Changed

- **Default `dependencies` adds `keyring>=25.0`** for the new
  resolution layer above. Pure-Python on its minimum backends.
- **Active test suite shrinks to 219 tests** (was 280 with the
  legacy files). The pre-existing failure
  `test_github_client_anonymous_emits_warning` lived in the legacy
  set and disappears from the active count.
- **`docs/security-model.md` Layer 3** updated to describe the new
  resolution chain and lazy-import discipline.
- **`docs/plugin-contract.md`** gains a "Scaffolding a new plugin"
  section.

### Migration

No required migration. Existing vault files keep working; nothing
needs to be rewritten. The keyring is additive — operators who
prefer not to use it can ignore the layer entirely (env vars and
the vault file still take precedence in the documented order).

If your CI / Docker image was installing `mimir-mcp[legacy]` to use
the deprecated aggregator, that extra is gone. Pin v0.2.x or use a
source checkout if you still need `server.py` / `native_tools/`.

## [0.2.0] — 2026-04-26 — security hardening from audit 2026-04-26-1242

This release closes the nine findings of the post-skill-fix red-team
audit (`docs/security/audit-2026-04-26-1242.md`) and the residual
debt that the Mode B automated workflow could not address mechanically.
No new features; existing plugin contract is unchanged unless noted
under **Breaking changes** below.

### Breaking changes

- **`core.profile.load_enabled_plugins` fail-closes on malformed
  profiles.** When the profile file is present but YAML-invalid,
  has a non-mapping top-level, or has a non-list `enabled_plugins`
  value, the function now returns `set()` (deny all plugins) instead
  of `None` (admit everything). A typo in `profiles/<name>.yaml` no
  longer silently widens the gate to every discovered plugin.

  **Migration:** if your profile is currently loading without error
  logs, no action needed. If you upgrade and see plugins in
  `disabled_by_profile` you didn't expect, run

  ```bash
  python -c "import yaml; yaml.safe_load(open('profiles/default.yaml'))"
  ```

  to surface the parse error and fix the YAML.

  Reference: `docs/security/audit-2026-04-26-1242.md` § VULN-02.
  Commit: `f50eead`.

### Security

- **Wheel no longer ships the legacy `server.py` aggregator nor
  `native_tools/`.** The pre-rename aggregator and its native tool
  helpers are still present in the source repo to keep the legacy
  test suite working locally, but `pip install mimir-router-mcp` only
  delivers `router` + `core/`. Operators of the deprecated aggregator
  must keep using a source checkout until the Fase 8 cleanup release.
  Reference: `docs/security/audit-2026-04-26-1242.md` § VULN-01.

- **Audit log size-based rotation.** `core.audit.log_tool_call` now
  rotates the active log when it exceeds `MIMIR_AUDIT_MAX_BYTES`
  (default 10 MB), keeping `MIMIR_AUDIT_BACKUP_COUNT` (default 7)
  backups. The previous version grew without bound despite the
  security model claiming daily rotation.
  Reference: VULN-04. Commit: `2cb490e`.

- **Plugin install/remove emit a dedicated security audit event.**
  When `router_install_plugin` / `router_remove_plugin` run with
  `execute=True` (gated behind `[security].allow_plugin_install =
  true` in `router.toml`), a high-visibility `_log.warning` and an
  audit entry with `status=security_event:plugin_install_executed`
  (or `_remove_executed`) fire before the action. Operators can grep
  the audit log for these events even when normal calls show
  `status=ok`.
  Reference: VULN-07. Commit: `294a5ac`.

- **Vault file write is now atomic.** `router_add_credential` writes
  to a sibling tempfile, fsyncs and `os.replace`s into place — a
  process death mid-flush no longer leaves the vault truncated.
  Reference: VULN-09. Commit: `791a18c`.

- **`SqliteMemory.search` escapes LIKE wildcards.** A query of `%`
  used to return every entry; `_` matched any single character.
  Both are now escaped with `ESCAPE '\'`.
  Reference: VULN-08. Commit: `3b8e806`.

- **Duplicate-secret detection logs to stderr.** When `secrets/*.md`
  contains the same key with divergent values, the previous
  `UserWarning`-only channel was easy to miss; the message now also
  goes through `logger.error`.
  Reference: VULN-06. Commit: `4d26993`.

- **`server.py` import emits `DeprecationWarning`.** The duplicate
  `server.legacy.py` (byte-identical) was deleted. `server.py`
  remains in the source repo for the legacy test suite but flags
  its deprecation on import.
  Reference: VULN-01. Commits: `7717d7b` (warning + duplicate drop)
  + this release (wheel exclusion).

### Documentation

- **Manifest fields scoped explicitly** (`core/loader.py` docstring
  and `plugins/_example/plugin.toml`): `inventory_access`,
  `network_dynamic`, `filesystem_read`, `filesystem_write` and
  `exec` are documented as **parsed but not yet enforced** — the
  enforcement is reserved for the future plugin runtime sandbox
  milestone (Layer 5). Plugin authors must not rely on them as a
  sandbox today.
  Reference: VULN-03. Commit: `8832a16`.

### Dependencies

- **`paramiko`, `pygithub`, `requests`, `pyserial` moved to a
  `legacy` extra.** They are only used by the deprecated
  `server.py` / `native_tools/`. Install with
  `pip install mimir-mcp[legacy]` if you depend on them.
  Reference: VULN-05. Commit: `534fd70`.

## [0.1.0] — 2026-04-25 — first public release

First public release of Mimir as a standalone framework. Born as
`homelab-fastmcp`, an MCP aggregator the author built for personal
homelab use, and reshaped into a generic declarative MCP router with
LLM-guided onboarding.

### Added

- **Declarative plugin contract**: every plugin ships its own
  `plugin.toml` describing identity, runtime, security boundary,
  inventory requirements, and tool whitelist/blacklist. Two runtime
  forms are supported: `entry = "server.py"` (plain Python script) and
  `command = "uv" / "uvx" / …` (custom launcher with `args` and
  `{plugin_dir}` placeholder substitution).
- **Inventory layer**: operators describe hosts and services in
  `inventory/*.yaml`. Plugins query the router for "hosts of type X"
  rather than hardcoding addresses or credentials.
- **LLM-guided onboarding**: the router exposes `router_help`,
  `router_status`, `router_add_host`, `router_add_service`,
  `router_add_credential`, plus dynamic `setup_<plugin>()` meta-tools
  for plugins waiting on inventory or credentials. The LLM walks the
  operator through the missing pieces conversationally.
- **Plugin lifecycle meta-tools**: `router_install_plugin`,
  `router_remove_plugin`, `router_enable_plugin`,
  `router_disable_plugin`, `router_list_plugins`. Strict mode (default)
  returns the exact shell command; permissive mode (opt-in via
  `[security].allow_plugin_install`) executes installs/removes
  directly.
- **Layered security model** (seven layers, see
  [`docs/security-model.md`](docs/security-model.md)):
  1. Manifest validation with quarantine for malformed `plugin.toml`.
  2. Centralised JSONL audit log at `config/audit.log`.
  3. Scoped credential vault — plugins request credentials by
     reference; the router checks the manifest before resolving.
  4. Profile gate — `profiles/<name>.yaml:enabled_plugins` allowlist.
  5. Tool whitelist/blacklist enforced by FastMCP middleware on
     `on_list_tools` and `on_call_tool`.
  6. Cross-plugin env scoping — credential-shaped vars only propagate
     to the subprocesses that claim them in `credential_refs`.
  7. Filesystem / network / exec interceptors — *deferred* until a
     real process sandbox lands.
- **Skill / agent discovery**: `.md` files with YAML frontmatter under
  `skills_dir` and `agents_dir` are exposed as `skill_<name>` /
  `agent_<name>` tools automatically.
- **Memory adapter pattern**: `MemoryBackend` interface with `noop`
  and `sqlite` backends; `engram` and `claude_mem` deferred.
- **Example plugin**: `examples/echo-plugin/` ships as a living minimal
  template, validated end-to-end by `tests/test_example_plugin.py`.

### Changed (since the legacy `homelab-fastmcp` 0.3.x line)

- **Renamed**: package, repo, brand → Mimir / `mimir-mcp`. Internal
  banner now reads `[mimir] router`.
- **`HOMELAB_DIR` → `MIMIR_HOME`**. The old name is still honoured
  with a `DeprecationWarning`. Default location follows platform
  conventions (`%APPDATA%/mimir` on Windows, `$XDG_CONFIG_HOME/mimir`
  elsewhere) instead of a hardcoded path.
- **`HOMELAB_FASTMCP_AUDIT_LOG` → `MIMIR_AUDIT_LOG`**. Same
  deprecation pattern.
- The architecture moved from a monolithic `server.py` to a modular
  `router.py` + `core/` package with discovery, manifest validation,
  scoped credentials, and FastMCP-based mounting. The legacy server
  remains under `server.legacy.py` and will be removed in a future
  release once all known consumers have migrated.

### Security

- `router_add_credential` rejects credential refs not declared by any
  loaded plugin and refuses values containing newlines or NUL bytes.
- Credentials are never written to the audit log — only a SHA-256
  hash of the ref is recorded.
- Path traversal blocked in `router_install_plugin` and
  `router_remove_plugin` via name validation plus `resolve()`-based
  containment checks.
