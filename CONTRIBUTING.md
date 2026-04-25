# Contributing to Mimir

🇬🇧 You're reading the English version · 🇪🇸 [Léeme en español](CONTRIBUTING.es.md)

Thanks for considering a contribution. Mimir is small and tries to stay
that way — the value lives in the contracts, not in the code volume.

## What kind of contributions are welcome

In rough order of urgency:

1. **Plugins**. Real ones. Mimir is interesting in proportion to the
   ecosystem of plugins it can mount. If you ship an MCP server, drop a
   `plugin.toml` and tell us; we'll add it to the list.
2. **Compatibility reports**. If you run Mimir against a client we
   haven't validated yet (Cline, Roo Code, Cursor, an in-house agent…)
   please open a PR adding a row to [`docs/compatibility.md`](docs/compatibility.md).
3. **Bug reports** with reproductions — see the issue templates.
4. **Documentation fixes**. Especially anywhere the framework still
   smells like the homelab it was born in.
5. **Core fixes**. Schema validation, security model, audit, env
   scoping — see [`docs/security-model.md`](docs/security-model.md)
   for the layered model so changes don't accidentally weaken a
   defence.

What's **out of scope** (won't merge):

- Web UI / dashboard. Mimir stays CLI-first; the LLM is the UX.
- Bundled domain plugins (Proxmox, GitHub, …). Those live in their own
  repos. Reference them, don't pull them in.
- Coupling to one specific client (Claude Desktop, Cursor, …). Mimir
  speaks MCP over stdio; that is the contract.

## Setting up

```bash
git clone https://github.com/CTRQuko/mimir-mcp
cd mimir-mcp
uv sync --extra test
uv run --extra test pytest tests/ -q
uv run python router.py --dry-run
```

If both pass, you're ready. The `tests/` suite must stay green at
every commit.

## Writing a plugin

Read [`docs/naming-guide.md`](docs/naming-guide.md) and copy
[`examples/echo-plugin/`](examples/echo-plugin/) as your template. The
contract is the `plugin.toml` schema in
[`docs/plugin-contract.md`](docs/plugin-contract.md).

Three rules a plugin must respect:

- **No hardcoded infrastructure**. IPs, hostnames, paths — none of that
  lives in plugin code. Either declare a `[[requires.hosts]]` and let
  the operator's inventory provide it, or accept a credential ref.
- **No direct `os.environ` reads for secrets**. Declare a
  `credential_refs` pattern in `[security]` — Mimir injects the
  matching vars into the subprocess and only those.
- **No imports from `core/`**. Plugins live in their own repos and
  must work standalone. Talk to Mimir through the manifest and
  through env vars it injects, not through framework internals.

## Code style

- Python 3.11+. We use `from __future__ import annotations` everywhere.
- Type hints on public functions.
- Tests live next to the module they cover (`core/foo.py` →
  `tests/test_core_foo.py`).
- Add a test before fixing a bug. The fix is the second commit; the
  failing test is the first.

## Pull requests

Small PRs merge fast. Big PRs sit until the author has time to review
them properly. If your change is bigger than ~300 lines, open an issue
first to discuss the shape.

A clean PR has:

- A focused diff — one concept per commit.
- A description that explains *why*, not *what* (the diff already
  shows the what).
- Tests for the behaviour change.
- A bullet under "Unreleased" in [`CHANGELOG.md`](CHANGELOG.md) if the
  change is user-visible.

CI must be green before merge.

## Security reports

Don't open public issues for security bugs. Email the maintainer
(see [SECURITY.md](docs/security-model.md) for the contact line; if
none is listed, GitHub's private vulnerability reporting feature is
fine).

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
Be excellent to each other.
