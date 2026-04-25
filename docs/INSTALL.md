# Install Mimir

🇬🇧 You're reading the English version · 🇪🇸 [Léeme en español](es/INSTALL.md)

Three install paths depending on what you want.

## 1. Run from a checkout (recommended for now)

The branch where this code lives is not yet on PyPI. Clone the repo
and run with `uv`:

```bash
git clone https://github.com/CTRQuko/mimir-mcp
cd mimir-mcp
uv sync
uv run python router.py --dry-run
```

The dry-run prints what Mimir would expose: inventory, plugins,
skills/agents. Without configuration it serves only the `router_*`
meta-tools — that's the empty baseline.

## 2. Add Mimir to an MCP client

Once the dry-run looks correct, point your MCP client at `router.py`.
Same pattern for any client that supports stdio MCP servers.

Example (Claude Desktop config — `%APPDATA%\Claude\claude_desktop_config.json`
on Windows, `~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS):

```json
{
  "mcpServers": {
    "mimir": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mimir-mcp",
        "run",
        "python",
        "router.py"
      ]
    }
  }
}
```

Restart the client. The first tool you'll see is `router_help` — call
it from your LLM and it will guide the rest.

## 3. Mount your first plugin

Plugins are MCP servers with a `plugin.toml`. The minimal example
ships with the framework:

```bash
ln -s "$(pwd)/examples/echo-plugin" plugins/echo
uv run python router.py --dry-run
```

For real-world manifests (uv-managed Python packages, uvx, declared
credential refs, declared host requirements), see
[`docs/operator-notes/cutover/manifests/`](operator-notes/cutover/manifests/).

## Configuration files

- **`config/router.toml`** — framework config (profile, paths to
  plugin dir / inventory dir / skills dir, memory backend choice).
  Optional: defaults work if absent.
- **`inventory/hosts.yaml`** + **`inventory/services.yaml`** — your
  declarative infrastructure. Templates live next to them as
  `*.yaml.example`.
- **`profiles/<name>.yaml`** — explicit allowlist of plugins for that
  profile. The default profile loads no plugins; create your own to
  activate them.
- **`<MIMIR_HOME>/secrets/*.md`** — optional vault file with
  `KEY=value` lines. `MIMIR_HOME` is the env var that points Mimir at
  its config root; default is `%APPDATA%/mimir` on Windows or
  `$XDG_CONFIG_HOME/mimir` (typically `~/.config/mimir`) elsewhere.
  The legacy name `HOMELAB_DIR` is still accepted with a
  deprecation warning so installs migrating from earlier prototypes
  don't break.

See [`docs/inventory-schema.md`](inventory-schema.md) for the YAML
shapes and [`docs/security-model.md`](security-model.md) for how
credentials flow.

## Sanity checks

```bash
# Tests
uv run --extra test pytest tests/ -q

# Dry-run shows current state without serving
uv run python router.py --dry-run
```

If both pass and the dry-run prints `[mimir] router — profile: …`,
the install is good.
