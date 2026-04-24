# Changelog

All notable changes to Mimir will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
