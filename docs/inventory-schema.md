# Inventory Schema

The framework has **no built-in knowledge** of the user's infrastructure.
Hosts and services are declared in `inventory/*.yaml` files; plugins ask the
router what exists, never hard-code addresses or credentials. This is the
single boundary where a user teaches the framework about their world.

## Files

- `inventory/hosts.yaml.example` — template, shipped in the repo. Neutral
  RFC 5737 placeholder addresses only.
- `inventory/hosts.yaml` — user-provided. **Gitignored.**
- `inventory/services.yaml.example` — template.
- `inventory/services.yaml` — user-provided. **Gitignored.**

The router reads whatever is in `inventory_dir` (configurable via
`router.toml`). If neither file exists, the inventory is empty — plugins
that have no host requirements still work; plugins that need hosts sit in
`pending_setup`.

## Hosts

```yaml
hosts:
  - name: myhost1                       # required, unique
    type: linux                         # linux | windows | macos | proxmox | …
    address: 192.0.2.10                 # IPv4/IPv6 or hostname
    port: 22                            # optional, plugin-specific default
    auth:
      method: ssh_key                   # ssh_key | password | api_token | jwt | …
      credential_ref: SSH_KEY_MYHOST1   # reference, NOT a value
    tags: [prod, database]              # optional, used by requires.hosts.tag
```

### Required fields

- `name` — unique identifier used by services (`host_ref`) and by humans.
- `type` — free-form string. The convention is lowercase, no spaces. Plugins
  declare which `type` values they accept.
- `address` — the router does not validate reachability, only format.

### Optional fields

- `port` — numeric, stored as-is.
- `auth.credential_ref` — name of an env var or vault entry. The raw value
  **never** appears in the YAML.
- `tags` — list of strings. `[[requires.hosts]]` can filter by tag.

## Services

```yaml
services:
  - name: mycontroller                  # required, unique
    type: unifi                         # free-form
    host_ref: myhost1                   # must match a host's `name`
    port: 11443                         # optional
    auth:
      method: jwt
      credential_ref: UNIFI_MYCONTROLLER_TOKEN
```

`host_ref` is validated at load time: a service pointing at a host that
doesn't exist raises `InventoryError`, which the router surfaces and refuses
to start rather than silently dropping the service.

## Writes via bootstrap tools

The LLM should not edit YAML directly. Four write paths exist, all going
through `core.bootstrap`:

- `router_add_host(name, type, address, port?, credential_ref?, auth_method?, tags?)`
- `router_add_service(name, type, host_ref, port?, credential_ref?, auth_method?)`
- `router_add_credential(ref, value)` — credential goes to the scoped vault
  (`$HOMELAB_DIR/.config/secrets/router_vault.md`), never to the YAML.
- Each write triggers `state.refresh()` so subsequent `router_status()`
  reflects the new state — the LLM does not need to restart anything.

## Plugin API — read-only

Plugins consume inventory via `core.inventory.Inventory`:

```python
inv.get_hosts(type="proxmox", tag="prod")   # filtered list of Host objects
inv.get_services(host_ref="myhost1")
inv.get_credentials("PROXMOX_*_TOKEN", plugin_ctx)  # gated by manifest
```

Plugins never open the YAML themselves; never see `os.environ`; never see
the vault file. All three go through the router so audit coverage is
complete and scope enforcement stays in one place.

## What is **not** in inventory

- Credential values. Only refs.
- Plugin-specific config beyond `name/type/address/port/auth/tags/host_ref`.
  A plugin that needs extra config reads its own file under its plugin dir,
  or asks the user via its `setup_<plugin>()` meta-tool to add a credential.
- The framework's own config (`router.toml`). That lives under `config/`.
