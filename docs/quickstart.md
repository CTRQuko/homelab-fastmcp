# Quickstart — what onboarding feels like

This document shows the experience Mimir is designed for: an operator
runs the router with no configuration and the LLM walks them through
the setup, conversation by conversation. No YAML editing, no manual
file creation, no documentation deep-dive before the first useful
call.

The transcript below is illustrative — exact phrasing depends on the
LLM and the client — but the **tool calls** are real. Every
`router_*` and `setup_*` invocation here is wired in the framework
and exercised by `tests/test_integration_e2e.py`.

## Step 0 — Install and start

```bash
pip install mimir-mcp
# or, from a checkout: uv run python router.py
```

Configure your MCP client (Claude Desktop, Cursor, Zed, …) to launch
Mimir over stdio (see [`docs/INSTALL.md`](INSTALL.md)). Restart the
client. That's all the pre-LLM setup there is.

## Step 1 — First contact

> **Operator** *(to the LLM in the client)*: I just installed Mimir.
> What can it do?

> **LLM**: I'll check.

```jsonc
// LLM calls
router_help()
```

```jsonc
// Mimir returns
{
  "name": "mimir",
  "purpose": "Mimir is a declarative MCP router. Users describe…",
  "available_bootstrap_tools": [
    "router_status", "router_help",
    "router_add_host", "router_add_service", "router_add_credential",
    "router_install_plugin", "router_remove_plugin",
    "router_enable_plugin", "router_disable_plugin",
    "router_list_plugins"
  ],
  "next_steps": [
    "Call router_status() to see what's currently configured.",
    "Add hosts and services via router_add_host / router_add_service."
  ]
}
```

> **LLM**: Mimir is running with no plugins yet. Want me to install
> one? Pick a service and I'll handle the rest.

## Step 2 — Install a plugin (strict mode, default)

> **Operator**: I want Proxmox tools.

> **LLM**: I'll install the Proxmox plugin.

```jsonc
router_install_plugin(source="github:CTRQuko/homelab-mcp")
```

```jsonc
{
  "action": "manual_instruction",
  "command": "git clone https://github.com/CTRQuko/homelab-mcp.git /…/plugins/homelab",
  "hint": "Run the command above on your machine. After it completes, restart Mimir."
}
```

> **LLM**: I can't run shell commands myself in strict mode (default
> for safety). Run this command in a terminal, then tell me when
> done:
>
> `git clone https://github.com/CTRQuko/homelab-mcp.git plugins/homelab`

> **Operator**: Done.

> **LLM**: Restart Mimir for the plugin to be picked up. *(Operator
> restarts the client, which respawns Mimir.)*

## Step 3 — Mimir spots the missing pieces

> **Operator**: What's the state now?

```jsonc
router_status()
```

```jsonc
{
  "memory_backend": "noop",
  "inventory": { "hosts_total": 0, "services_total": 0 },
  "plugins": [
    {
      "name": "homelab",
      "version": "1.0.0",
      "status": "pending_setup",
      "missing": [
        { "kind": "hosts", "detail": { "type": "proxmox", "min": 1 },
          "prompt": "At least one Proxmox node with an API token…" },
        { "kind": "credentials", "detail": { "pattern": "PROXMOX_*" },
          "prompt": "Proxmox credentials (PROXMOX_<NODE>_HOST / _USER / _TOKEN)…" }
      ]
    }
  ],
  "setup_pending": ["homelab"]
}
```

> **LLM**: The Proxmox plugin needs a node and credentials. Let me ask
> what you have.
>
> ```jsonc
> setup_homelab()
> ```
>
> Returns: *"Need: 1 Proxmox node + credentials matching PROXMOX_\*."*
>
> What's the IP of your Proxmox node?

## Step 4 — Operator answers, LLM writes inventory

> **Operator**: 192.0.2.10, port 8006, name pve1.

> **LLM**: Got it.

```jsonc
router_add_host(
  name="pve1",
  type="proxmox",
  address="192.0.2.10",
  port=8006,
  credential_ref="PROXMOX_PVE1_TOKEN",
  auth_method="api_token"
)
```

```jsonc
{ "added": "pve1", "path": "inventory/hosts.yaml" }
```

> **LLM**: Now I need the API token. Create one in the Proxmox UI
> (Datacenter → Permissions → API Tokens) with VM.Audit +
> VM.PowerMgmt. Paste it here when ready.

> **Operator**: PVEAPIToken=root@pam!claude=abc123def456…

> **LLM**: Storing it in the vault, never in the audit log.

```jsonc
router_add_credential(ref="PROXMOX_PVE1_TOKEN", value="<redacted>")
```

```jsonc
{
  "ref": "PROXMOX_PVE1_TOKEN",
  "stored_at": "<MIMIR_HOME>/secrets/router_vault.md",
  "audited": "ref-only, value never logged"
}
```

## Step 5 — Plugin activates

> **LLM**: Refreshing state.

```jsonc
router_status()
```

```jsonc
{
  "plugins": [
    {
      "name": "homelab",
      "version": "1.0.0",
      "status": "ok",
      "missing": []
    }
  ],
  "setup_pending": []
}
```

> **LLM**: The Proxmox plugin is live. You can now ask things like
> *"list nodes"*, *"start VM 100"*, *"check storage on pve1"*.

## What just happened

The operator never:

- Edited a YAML file by hand.
- Looked up the credential storage path.
- Read a "configure your inventory" tutorial.
- Knew the difference between a `host`, a `service`, a `credential ref`.

The LLM did all of that *through* Mimir's meta-tools. The framework's
job is to make those tools precise enough that the LLM can drive them
without surprises — and safe enough that a misstep doesn't leak
secrets or run arbitrary code without consent.

That's the design center. Everything else (security layers, manifest
schema, plugin contract) exists to make this conversation possible
without compromising on either of those.

## See also

- [`README.md`](../README.md) — what Mimir is and how to install.
- [`docs/INSTALL.md`](INSTALL.md) — install paths and config.
- [`docs/security-model.md`](security-model.md) — how secrets stay
  out of the audit log and the client transcript.
- [`docs/plugin-contract.md`](plugin-contract.md) — what plugin
  authors declare so the LLM can guide an operator through their
  setup.
- [`examples/echo-plugin/`](../examples/echo-plugin/) — minimal
  template a plugin author starts from.
