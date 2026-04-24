# Mimir architecture

A single-process Python MCP router built on FastMCP 3.x. The shape:

```
┌─────────────┐  stdio  ┌────────────────────────────┐
│ MCP client  │ ◄─────► │ router.py (Mimir)          │
│ (Claude     │         │  ├── core/                 │
│  Desktop,   │         │  │   secrets, audit,       │
│  agent…)    │         │  │   inventory, memory,    │
└─────────────┘         │  │   loader, profile,      │
                        │  │   skills                │
                        │  ├── meta-tools (router_*) │
                        │  ├── tool middleware       │
                        │  └── mounted plugins       │
                        │      via create_proxy      │
                        └────────────────────────────┘
                              ▲   ▲   ▲   ▲
                              │   │   │   │   subprocess
                              │   │   │   │   spawned per
                              │   │   │   │   plugin
                              ▼   ▼   ▼   ▼
                        ┌────┐┌────┐┌────┐┌────┐
                        │ p1 ││ p2 ││ p3 ││ pN │  plugins
                        └────┘└────┘└────┘└────┘
```

## Pieces

### `router.py`

Entry point. On startup it builds a `RouterState` (config + inventory
+ discovered plugins), then assembles a `FastMCP` instance with:

- **Meta-tools** prefixed `router_*` and `setup_<plugin>()` for the
  LLM to drive onboarding.
- **Mounted plugins** as FastMCP subservers via `create_proxy`, each
  under its own namespace.
- **Skills/agents** discovered as `.md` files with frontmatter,
  exposed as `skill_<name>` / `agent_<name>`.
- **Tool policy middleware** that filters `[tools].whitelist/blacklist`
  declarations on `on_list_tools` and `on_call_tool`.

### `core/`

The infra-agnostic building blocks. None of these expose tools
directly — they're consumed by the router and by plugins indirectly:

- `secrets` — scoped credential vault. `get_credential(ref, ctx)`
  with manifest-declared allowlist.
- `audit` — JSONL append-only log of every tool call.
- `inventory` — typed `Host` / `Service` reader for `inventory/*.yaml`.
- `memory` — adapter pattern; `noop` and `sqlite` implemented,
  `engram` / `claude_mem` deferred.
- `loader` — manifest parser, requirement evaluation, reconciliation
  diff, quarantine for malformed `plugin.toml`.
- `profile` — reads `profiles/<name>.yaml:enabled_plugins` allowlist.
- `skills` — discovers `.md` files with YAML frontmatter.

### Plugins (under `plugins/`)

Each plugin is a directory with at least a `plugin.toml`. The router
spawns each plugin as a subprocess via FastMCP's `create_proxy` and
prefixes its tools with the plugin's namespace. The subprocess's env
is built explicitly: ordinary system vars pass through; credential-
shaped vars only propagate when the plugin's manifest claims them
(see `docs/security-model.md` for cross-plugin scoping).

Plugins are independent: each one can run standalone as an MCP server.
Mimir aggregates them but does not own them.

### Inventory (under `inventory/`)

The operator's declarative description of their infrastructure.
`hosts.yaml` lists hosts (type, address, auth method, credential
ref, tags); `services.yaml` lists services and ties them to hosts.
Plugins ask the router *"give me hosts of type X"* — they never read
this directory directly.

### Profiles (under `profiles/`)

`<name>.yaml:enabled_plugins` is a strict allowlist. The default
profile loads no plugins; the operator creates additional profiles
to enable the plugins they want.

## The startup sequence

1. Parse `config/router.toml` → `RouterConfig`.
2. Load inventory from `inventory/*.yaml` → `Inventory`.
3. Discover plugins under `plugins/`:
   - Parse each `plugin.toml`. Malformed → quarantine.
   - Evaluate requirements against the inventory → set status
     (`ok` / `pending_setup` / `disabled` / `error`).
4. Apply the profile gate: drop plugins not in `enabled_plugins`.
5. Bootstrap the FastMCP instance:
   - Register `router_*` and `setup_<plugin>()` meta-tools.
   - Mount each `ok` plugin via `create_proxy` with scoped env.
   - Discover skills/agents and register one tool per file.
   - Attach the tool policy middleware.
6. Serve over stdio (or print and exit if `--dry-run`).

## Where the contract lives

- **`plugin.toml`** — the schema is in
  [`docs/plugin-contract.md`](plugin-contract.md). The example plugin
  in [`examples/echo-plugin/`](../examples/echo-plugin/) is the
  living minimal reference.
- **`inventory/*.yaml`** — schema in
  [`docs/inventory-schema.md`](inventory-schema.md).
- **Naming** — conventions in
  [`docs/naming-guide.md`](naming-guide.md).
- **Security layers** — full breakdown in
  [`docs/security-model.md`](security-model.md).

## Out of scope (today)

- Process sandbox beyond what the OS provides. Layer 5 tier 2
  (filesystem / network / exec interceptors) is deferred until a
  real sandbox lands.
- Web UI or REST API. Stdio + LLM are the supported control planes.
- Plugin venv management (`[runtime].venv = "auto"` + `deps = [...]`
  install). Plugins bring their own environment for now.
