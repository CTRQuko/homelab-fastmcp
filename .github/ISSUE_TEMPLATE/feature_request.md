---
name: Feature request
about: Suggest a change to the framework itself
title: "[feature] "
labels: enhancement
assignees: ""
---

## The need

<!-- One paragraph: what use case is currently painful or impossible? -->

## Proposed shape

<!-- How would the change show up to the operator / plugin author?
     A snippet of the new plugin.toml or router_* tool signature is
     more useful than a paragraph of prose. -->

## Why it belongs in core

Mimir tries to stay small. A change is in scope when:

- It is **infrastructure-agnostic**. Nothing that hardcodes a specific
  vendor / cloud / homelab.
- It improves a **contract** (manifest, inventory, security model)
  rather than adding a feature only one plugin would use.
- The same problem cannot be solved cleanly in a plugin.

If your idea would couple Mimir to one ecosystem, it probably wants to
live in a plugin instead.

## Alternatives considered

<!-- What else did you try? Why didn't it work? -->
