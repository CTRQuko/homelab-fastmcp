# Plugin Contract

Every plugin lives in its own directory under `plugins/<name>/` and exposes a
manifest called `plugin.toml`. The router walks `plugins/` at startup, parses
every manifest, and evaluates each plugin's requirements against the
declarative inventory. Plugins do not load unless their manifest parses; they
do not activate unless their requirements are met and the active profile
allows them.

## Directory layout

```
plugins/
  myplugin/
    plugin.toml         # required
    server.py           # [runtime].entry — the plugin implementation
    requirements.txt    # optional; referenced via [runtime].deps
```

Anything starting with `.` or `_` is skipped (used for examples and scaffolding
that should not activate).

## Manifest sections

### `[plugin]` — identity (required)

```toml
[plugin]
name    = "proxmox"       # required. Used as the plugin id everywhere
version = "1.0.0"         # required
enabled = true            # optional, default true
```

`enabled = false` keeps the manifest visible in `router_status()` but the
plugin never activates — useful when you want the scaffolding present but
staged for later.

### `[runtime]` — how the plugin runs

```toml
[runtime]
entry  = "server.py"          # file the router should import
python = ">=3.11"             # informational; checked against the host
deps   = ["proxmoxer>=2.0"]   # third-party deps; resolved via uv/pip
venv   = "auto"               # "auto" | "shared" | specific path
```

This is informational for now — actual subprocess/import mounting is
scheduled for a later phase. The fields are parsed and preserved so the
contract is stable for plugin authors today.

### `[security]` — declared capabilities (required under `strict_manifest`)

```toml
[security]
inventory_access  = ["hosts:type=proxmox", "credentials:PROXMOX_*_TOKEN"]
credential_refs   = ["PROXMOX_*_TOKEN"]   # glob patterns the plugin may read
network_dynamic   = true                  # may open arbitrary TCP/UDP sockets
filesystem_read   = []                    # paths allowed for reads
filesystem_write  = []                    # paths allowed for writes
exec              = []                    # subprocess commands allowed
```

`strict_manifest = true` in `config/router.toml` (the default) means a missing
`[security]` table quarantines the plugin. `credential_refs` is the source of
truth for `router_add_credential`: the user can only save a credential whose
ref matches at least one loaded plugin's patterns.

### `[requires]` — what the plugin needs before it activates

```toml
[[requires.hosts]]
type   = "proxmox"        # matched against Host.type in inventory
tag    = "prod"           # optional; matched against Host.tags
min    = 1                # default 1
prompt = "Need a Proxmox node with API token"

[[requires.credentials]]
pattern = "PROXMOX_*_TOKEN"   # fnmatch pattern against env or vault
prompt  = "API token with VM.Audit + VM.PowerMgmt"
```

A plugin with one or more unmet requirements goes to `pending_setup`. The
router then exposes a dynamic `setup_<plugin>()` tool whose output includes
each `prompt` verbatim, so an LLM can guide the user through the exact
inputs that are missing.

### `[tools]` — optional allow/deny list

```toml
[tools]
whitelist = ["*"]              # empty/absent means allow all
blacklist = ["destroy_*"]      # checked first; always denies
```

Matching uses `fnmatch` (case-sensitive). Helper: `core.loader.tool_allowed`.
This is enforced at mount time when the router actually loads plugin tools;
the helper is in place today and the enforcement lands when subprocess/import
mounting does.

## Lifecycle states

| Status                 | Meaning                                                                    |
| ---------------------- | -------------------------------------------------------------------------- |
| `ok`                   | Requirements met; tools available.                                         |
| `pending_setup`        | Manifest loaded but `[requires]` unmet. `setup_<plugin>()` is exposed.     |
| `disabled`             | Manifest says `enabled = false`.                                           |
| `disabled_by_profile`  | Active profile's `enabled_plugins` allowlist excludes the plugin.          |
| `error`                | Plugin failed to start after reaching `ok` (runtime errors).               |
| `quarantined`          | `plugin.toml` failed to parse — visible in `LoadReport.quarantined`.       |

Malformed TOML no longer aborts discovery: siblings still load. The broken
directory is recorded so the user can see what is wrong without the router
refusing to start.

## Diff detection

`config/.last_state.json` stores the set of plugin names that activated on
the previous run. On each `reconcile()` the router reports `added`,
`removed`, and `unchanged`. Dropping in a new plugin directory or deleting
an existing one is visible at next startup — no restart flag needed.
