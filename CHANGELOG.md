# Changelog

All notable changes to Mimir will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-04-26 — Fase 8 cleanup + keyring (MVP) + plugin scaffolder

This release closes the Fase 8 cleanup promised in v0.2.0 and adds two
new capabilities: OS keyring as a credential source, and a tool to
scaffold new plugin manifests from MCP. No new breaking changes for
existing API consumers; the additions are additive.

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
  test suite working locally, but `pip install mimir-mcp` only
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
