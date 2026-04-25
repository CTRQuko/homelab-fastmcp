# Security Model

🇬🇧 You're reading the English version · 🇪🇸 [Léeme en español](es/security-model.md)

The framework assumes plugins are untrusted code. Four layers of defence are
in place today; a fifth (runtime interceptors) is scoped for a later phase.

## Layer 1 — Manifest validation

Every `plugin.toml` is parsed at startup. Missing or malformed sections
move the plugin to **quarantine** instead of crashing the router. Under
`strict_manifest = true` (the default), a `[security]` section is
mandatory.

A quarantined plugin is visible in `router_status()` and the startup log,
so the user can see what went wrong — but it **does not load** and its
declared capabilities do **not** widen any allowlists. Dropping a
malicious `plugin.toml` into `plugins/` does not give it credential access.

## Layer 2 — Centralised audit log

`core.audit.log_tool_call()` appends one JSON line per tool invocation to
`config/audit.log`. Entries contain timestamp, plugin, tool name, an
SHA-256 hash of the arguments (never the raw values), duration, and
status. Writes are fire-and-forget so an audit failure cannot block a tool
call, and rotation is daily by date.

Every tool the router exposes is wrapped: the `router_*` meta-tools, each
`setup_<plugin>()`, and the `skill_*`/`agent_*` discovery tools. Error
paths record `status = "error:<ExceptionType>"` so failed calls show up
in the log instead of disappearing silently. When a plugin mount lands
(see Layer 5), it inherits the same contract — its tools must route
through the audit-wrapped registration helper.

**Secrets are never logged.** `router_add_credential` deliberately omits
the `value` field from the audit dict; only `ref` is hashed. Any plugin
author copying this pattern must do the same.

## Layer 3 — Scoped credential vault

Credentials never live in plain YAML and are never read from `os.environ`
by plugins. They are requested through
`core.secrets.get_credential(ref, plugin_ctx)`, which:

1. Verifies the plugin's manifest declares a `credential_refs` pattern
   matching `ref`.
2. Looks up the value in this order: env var → vault file
   (`$MIMIR_HOME/secrets/router_vault.md`) → miss.
3. Records the access in audit (hash of ref only).

Writes go through `router_add_credential`, which:

- Rejects refs that don't match `^[A-Z][A-Z0-9_]{2,63}$`.
- Rejects values containing newline or NUL characters (prevents injection
  of a second key/value escaping the caller's scope).
- Rejects refs that don't match any loaded plugin's `credential_refs`
  patterns — no dumping arbitrary secrets into the vault.
- Sets mode 0o600 on POSIX.

Plugins marked `disabled` or `error` do **not** contribute patterns to the
allowlist, so a disabled manifest cannot widen credential scope.

### Scoping for subprocess plugins

Plugins mounted via `create_proxy` (Fase 6b) run as child processes, so
the in-process `get_credential` check does not reach them — they read env
directly. The router therefore *builds* each subprocess's env explicitly
(`router._plugin_subprocess_env`):

- Ordinary system vars (`PATH`, `APPDATA`, `HOME`, `PYTHON*`, `MIMIR_HOME`,
  etc.) pass through so the child interpreter can start.
- Credential-shaped vars (uppercase + underscore, length ≥ 3) only
  propagate when they match **this plugin's** `credential_refs` patterns,
  **or** are not claimed by any other loaded plugin. A var claimed by
  plugin B but not by plugin A is stripped from A's subprocess env.
- Refs stored only in `secrets/*.md` or `.env` (not in `os.environ`) are
  resolved via `core.secrets.resolve_refs_matching` and merged in, so the
  subprocess sees the same view the vault API would return.

Net effect: sibling subprocess plugins cannot see each other's tokens
even though they share the same router process.

## Layer 4 — Profile gate

`profiles/<name>.yaml` is an explicit allowlist of plugin names that may
activate. This runs **after** requirement evaluation, so:

- Empty file or missing `enabled_plugins` key → no gate, every discovered
  plugin can load.
- `enabled_plugins: []` → no plugin loads; only core + meta-tools visible.
  This is the default in `profiles/default.yaml`.
- `enabled_plugins: [a, b]` → only `a` and `b` activate; all others go to
  `disabled_by_profile`.

The gate is a second allowlist on top of the manifest — even if a plugin's
requirements are met, the profile can refuse it. Flipping profiles is the
fastest way to reduce the exposed tool surface without editing plugins.

## Layer 5 — Runtime interceptors

Enforcement is split in two because the two surfaces have very different
cost profiles:

- **`[tools].whitelist/blacklist`** — **enforced.** A single FastMCP
  middleware (`router._make_tool_filter_middleware`) consults a
  per-namespace policy dict built at `build_mcp` time. It runs on two
  hooks:
  - `on_list_tools`: denied tools are stripped from the response so the
    LLM never sees them. Reduces token surface and attack surface.
  - `on_call_tool`: a client that calls a denied name anyway (stale
    list cache, malicious client) gets a clean `ValueError` instead of
    the tool running. Defence in depth.

  Patterns in the manifest are matched against the **local** tool name
  (namespace prefix stripped), so `blacklist = ["destroy_vm"]` blocks
  `<plugin>_destroy_vm`. Tools outside any mounted plugin's namespace
  (`router_*`, `skill_*`, core meta-tools) always pass — the filter is
  scoped strictly to plugin-declared tools.
- **`network_dynamic`, `filesystem_read`, `filesystem_write`, `exec`** —
  still planned. Require interception at subprocess/socket/pathlib
  boundaries. Done in-process these are advisory at best (any plugin
  can monkeypatch them back); done correctly they need the plugin to
  run in a child process the router controls. Scheduled together with
  the plugin runtime sandbox, not before.

## Threat model summary

| Threat                                                        | Layer | Mitigation                                                                    |
| ------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------- |
| Malicious `plugin.toml` pulls in arbitrary credentials         | 1+3   | Quarantine on parse error; disabled plugins don't widen credential allowlist. |
| Plugin logs a secret by accident                               | 2     | `router_add_credential` never logs value; audit hashes only.                   |
| Plugin reads `.env` directly                                   | 3     | Credentials resolved via `core.secrets` with scope check; plugins never see raw env. |
| Newline injection in credential value escapes scope            | 3     | Control chars rejected at write time.                                          |
| User exposes more tools than intended                          | 4     | `profiles/<name>.yaml` allowlist; empty profile = only core tools.             |
| Plugin exposes a tool the operator does not want invokable     | 5     | `[tools].whitelist/blacklist` enforced by FastMCP middleware on list + call.   |
| Plugin wants filesystem outside declared `filesystem_read`     | 5     | Planned. Needs process boundary — scheduled with plugin runtime sandbox.       |
| Plugin exfiltrates data via arbitrary sockets                  | 5     | Planned. Same phase as above.                                                  |

## Known limitations

Two constraints are inherent to the MCP stdio transport and cannot be fully
fixed inside the router. They are flagged here so plugin authors and
operators can plan around them.

### `setup_<plugin>()` tools persist for the whole session

MCP stdio has no way for a server to de-register a tool after the handshake.
Once `setup_<plugin>()` is exposed at startup (because the plugin was
`pending_setup`), the client keeps seeing it for the rest of the session,
even after the plugin reaches `ok`.

The router mitigates this by reading *live* state inside the tool: a
completed plugin returns `{"status": "ok", "missing": []}` so the LLM sees
the setup is done. The tool itself stays exposed, but it no longer lies.
Use `router_status()` as the single source of truth for the active plugin
set; treat `setup_*` tools as one-plugin shortcuts rather than a reliable
"pending" signal.

### Credential values travel through the MCP client

When an LLM calls `router_add_credential(ref, value)`, the `value` argument
is serialised by the MCP client before reaching the router. The router's
audit log never writes the value (only a hash of the `ref`), but any
client-side transcript or tool-call log records the argument as-is. For
most MCP clients this means the raw credential will sit in conversation
history.

If that is unacceptable, add credentials out-of-band: either write them
directly into `$MIMIR_HOME/secrets/router_vault.md` (same `ref =
value` format, one per line, mode `0o600`) or set the matching environment
variable before starting the router. The router's `get_credential()` lookup
order is env var → vault file, so either path works without the LLM ever
seeing the value.

## Non-goals

- **Sandbox escape protection** — the framework is not a security sandbox.
  Determined malicious code running in the router process can subvert any
  Python-level check. The layers above raise the cost of accidental
  damage and narrow the scope of trusted plugins; they do not stop a
  hostile plugin that the user installs and activates on purpose.
- **Credential rotation** — the vault is a store, not a rotation manager.
- **Audit of reads outside MCP tools** — only tool invocations are
  audited; plugin internals are not instrumented.
