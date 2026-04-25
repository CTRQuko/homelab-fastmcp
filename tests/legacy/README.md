# Legacy tests

These tests exercise the **`server.py` legacy aggregator**, not Mimir.
They rely on:

- `server.py` running with all native_tools/ wired in.
- Downstream MCP servers physically installed under
  `C:/homelab/mcp-servers/` (homelab-mcp, gpon-mcp, mcp-uart-serial,
  unifi-mcp-server).
- A specific platform (some tests are Windows-only).

That makes them **operator-machine-only**. CI on a fresh GitHub
runner cannot satisfy any of those preconditions, so they are
isolated under `tests/legacy/` and skipped during the default test
run via the `norecursedirs = ["legacy", …]` entry in
`pyproject.toml`.

## When to run them

The author runs them **locally on a configured Windows machine**
before any production change to `server.py` or the legacy
downstreams:

```bash
pytest tests/legacy/ -q
```

After the legacy cleanup phase (post-cutover), `server.py` and
`native_tools/` get deleted and these tests go with them. Until
then they are kept as a regression net for the operator's specific
deployment.

## Default suite (CI + everyone else) does NOT run these

`pytest tests/ -q` recurses everything under `tests/` **except**
this directory. CI matrix on Linux + Windows GitHub runners runs
the agnostic Mimir suite — 313 tests + 2 skipped — and ignores
this folder.

## Tests in scope

- `test_integration.py` — asserts the legacy aggregator exposes
  `windows_*`, `linux_*`, `proxmox_*`, `docker_*`, `unifi_*`,
  `uart_*`, `gpon_*`, `tailscale_*` namespaces. Requires every
  downstream MCP installed and reachable.
- `test_env_propagation_downstream.py` — `_build_subprocess_env`
  contract on the legacy server's wholesale env replacement
  pattern.
- `test_adaptive.py` — cross-platform branches in `server.py`
  (Windows-only tools, Linux fallback paths).
- `test_resilience.py` — behaviour when downstream MCPs fail or
  are unreachable.
