# Mimir

[![CI](https://github.com/CTRQuko/mimir-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/CTRQuko/mimir-mcp/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mimir-mcp.svg)](https://pypi.org/project/mimir-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/mimir-mcp.svg)](https://pypi.org/project/mimir-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

🇬🇧 You're reading the English version · 🇪🇸 [Léeme en español](README.es.md)

> *"Mimir guarda el pozo de la sabiduría: aconseja a Odín cuando le falta
> contexto. Tu router MCP hace lo mismo con el LLM."*

**Mimir is a declarative MCP router** built on FastMCP 3.x. Drop a
`plugin.toml` next to any MCP server and Mimir discovers it, mounts it
under its own namespace, scopes its credentials, filters its tools, and
exposes the union to your client (Claude Desktop, an agent in a loop,
anything that speaks MCP over stdio).

What sets Mimir apart from other MCP aggregators:

- **Declarative plugin contract** — every plugin ships its own
  `plugin.toml` describing identity, runtime, security, requirements.
  No central config to keep in sync.
- **Inventory separated from plugins** — operators describe their
  infrastructure in `inventory/*.yaml` (hosts, services). Plugins ask
  the router *"give me hosts of type X"* — they never hardcode IPs or
  hostnames.
- **LLM-guided onboarding** — when a plugin needs hosts or credentials
  the operator hasn't supplied, the router exposes a `setup_<plugin>()`
  meta-tool. The LLM walks the operator through what's missing,
  conversation by conversation.
- **Layered security** — manifest quarantine, audit log, scoped
  credential vault, profile gate, tool whitelist/blacklist, and
  cross-plugin env scoping in subprocess. See
  [`docs/security-model.md`](docs/security-model.md).

## Quick install

```bash
git clone https://github.com/CTRQuko/mimir-mcp
cd mimir-mcp
uv sync
uv run python router.py --dry-run
```

The dry-run prints what Mimir sees: inventory, discovered plugins,
skills/agents. Without configuration it serves only meta-tools — that's
intentional. Drop your first plugin under `plugins/` and re-run.

## Hello world — the minimal plugin

Look at [`examples/echo-plugin/`](examples/echo-plugin/) to see the full
contract in ~30 lines. To mount it:

```bash
ln -s "$(pwd)/examples/echo-plugin" plugins/echo
uv run python router.py --dry-run
```

Output:

```
[mimir] router — profile: default
[mimir] Core: inventory, secrets, audit, memory(noop)
[mimir] Inventory: 0 hosts, 0 services
[mimir] Plugins discovered: 1
[mimir] Skills: 0  Agents: 0
```

Now the client sees `echo_echo` and `echo_reverse` alongside the
`router_*` meta-tools.

## How it compares

There are several MCP aggregators in the wild. Mimir occupies a specific
slot:

| Tool | What it adds | Where Mimir differs |
|------|--------------|---------------------|
| **FastMCP `mount()`** | Library to mount subservers in code | Mimir adds discovery, manifest schema and security layers on top |
| **MetaMCP** | Aggregator + middleware in Docker, three-level hierarchy | Mimir is a single-process Python router, focused on declarative plugin contracts and LLM-guided onboarding |
| **Local MCP Gateway** | Aggregator with Web UI, OAuth, profiles | Mimir is CLI-first and focused on the plugin contract — no UI, no OAuth (yet) |
| **mcp-proxy-server** | Routes requests to backend servers | Mimir adds the inventory layer and semantic requirements |
| **mxcp** | Build MCP servers from YAML/SQL/Python | Mimir aggregates already-built servers; complementary, not competing |

## Documentation

- [`docs/naming-guide.md`](docs/naming-guide.md) — canonical naming
  conventions for plugins, repos and tools.
- [`docs/plugin-contract.md`](docs/plugin-contract.md) — full reference
  for the `plugin.toml` schema.
- [`docs/inventory-schema.md`](docs/inventory-schema.md) — the YAML
  format operators use to declare hosts and services.
- [`docs/security-model.md`](docs/security-model.md) — the seven
  security layers in detail.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the router,
  core modules and plugins fit together.
- [`docs/INSTALL.md`](docs/INSTALL.md) — installation paths beyond the
  Quick install above.
- [`docs/operator-notes/`](docs/operator-notes/) — notes from one real
  deployment (the author's homelab). Useful as a worked example;
  **not** part of the public contract.

## Status

Mimir is a working framework with a 280+ test suite covering the core
modules, the loader, the router wiring, the security model, the
example plugin and the cutover manifests. It is being used in
production by the author against a real homelab. The branch
`refactor/generify-naming` (this code) is not yet published; the
public release is gated on the cutover described under
`docs/operator-notes/cutover/`.

## Project goals (and non-goals)

**Goals:**

- A small, readable router that anyone can audit in an evening.
- Plugins as truly independent MCP servers — usable standalone or
  mounted.
- Onboarding that an LLM can carry the operator through.
- Security defaults that the operator can reason about.

**Non-goals:**

- A full PaaS or sandbox. Mimir trusts the OS for process isolation;
  layer 5 tier 2 (filesystem/network/exec interceptors) is explicitly
  deferred until a real sandbox lands.
- A web UI. CLI + LLM are the supported control planes.
- Replacing FastMCP. Mimir is built on top of it.

## License

MIT. See [`LICENSE`](LICENSE).

## Acknowledgements

- The [Model Context Protocol](https://modelcontextprotocol.io) team
  at Anthropic for the standard.
- [FastMCP](https://github.com/jlowin/fastmcp) for the Python building
  blocks.
- The author's prima Claude, for asking *"is this for one homelab or
  for an ecosystem?"* at the right moment.
