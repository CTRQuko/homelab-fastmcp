---
name: Plugin question
about: Questions about writing or mounting a plugin
title: "[plugin] "
labels: question
assignees: ""
---

> **Quick checks before opening this:**
>
> - [`docs/naming-guide.md`](../../docs/naming-guide.md) — naming
>   conventions and the three runtime forms.
> - [`docs/plugin-contract.md`](../../docs/plugin-contract.md) — the
>   `plugin.toml` schema reference.
> - [`examples/echo-plugin/`](../../examples/echo-plugin) — minimal
>   working template.

If your question is about how to make Mimir mount your existing MCP
server: 95% of the time the answer is *"add this `plugin.toml` next
to your server entry point"* — the other 5% is what these issues are
for.

## What I'm trying to do

<!-- One sentence. -->

## What I have

```toml
# Your current plugin.toml (redact secrets)
```

```text
# What `router.py --dry-run` says about it
```

## What I expected vs got

<!-- Two sentences. Helps avoid the XY problem. -->
