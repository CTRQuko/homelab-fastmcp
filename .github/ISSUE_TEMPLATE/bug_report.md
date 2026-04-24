---
name: Bug report
about: Something in Mimir broke or behaves unexpectedly
title: "[bug] "
labels: bug
assignees: ""
---

## What happened

<!-- One or two sentences. What did you expect, what did you get? -->

## Reproduction

Smallest steps that reproduce the behaviour. Include:

1. The command you ran (e.g. `uv run python router.py --dry-run`).
2. The relevant `plugin.toml` / `inventory/*.yaml` / `router.toml`
   if any (redact secrets).
3. The output you got.

```text
<paste output here>
```

## Environment

- Mimir version: <!-- output of `pip show mimir-mcp` or commit sha -->
- Python version:
- OS:
- MCP client (Claude Code CLI / Claude Desktop / Cursor / Zed / …):
- Plugins mounted (names + versions):

## Logs

If `config/audit.log` has a relevant entry, paste it. Same for any
stderr the router emitted at startup.

## Have you already

- [ ] Run `router.py --dry-run` and read the banner?
- [ ] Checked `router_status()` from the client?
- [ ] Searched [open and closed issues](../issues?q=is%3Aissue) for
      the same symptom?
