"""Profile loader — gates which discovered plugins actually activate.

``profiles/<name>.yaml`` looks like::

    enabled_plugins:
      - proxmox
      - github

Semantics:

- **Missing file**: ``None`` is returned and the router treats every
  discovered plugin as enabled — this is "no gate configured by design".
- **Missing ``enabled_plugins`` key**: same as above (``None``). The
  operator wrote a profile but did not opt into the gate.
- **Empty list (``[]``)**: no plugins activate — only core + meta-tools
  are exposed. This is the default shipped in ``profiles/default.yaml``.
- **Non-empty list**: only plugins whose manifest name appears here
  activate; everything else is marked ``disabled_by_profile``.
- **Malformed YAML / wrong shape**: ``set()`` is returned (fail-closed —
  deny all plugins). Previously this branch returned ``None`` ("admit
  everything"), so a typo in the YAML silently widened the gate to
  every discovered plugin.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)


def load_enabled_plugins(profile_path: Path) -> set[str] | None:
    """Return the set of plugin names the profile allows, or ``None``.

    ``None`` means "no gate configured — admit everything".
    A returned ``set()`` (empty set) means "deny all plugins" — either
    because the operator wrote ``enabled_plugins: []`` deliberately, or
    because the file is present but unparseable (fail-closed).
    """
    if not profile_path.exists():
        return None  # By design: no profile = admit everything.
    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        # Profile present but YAML invalid — fail-closed. A typo in the
        # gate must NOT silently widen it to every plugin discovered.
        _log.error(
            "profile %s is malformed (%s) — denying all plugins",
            profile_path,
            exc,
        )
        return set()
    if not isinstance(data, dict):
        _log.error(
            "profile %s top-level must be a mapping — denying all plugins",
            profile_path,
        )
        return set()
    if "enabled_plugins" not in data:
        return None  # Operator wrote a profile but did not opt into the gate.
    value = data.get("enabled_plugins")
    if value is None:
        return set()
    if not isinstance(value, list):
        _log.error(
            "profile %s 'enabled_plugins' must be a list, got %s — denying all plugins",
            profile_path,
            type(value).__name__,
        )
        return set()
    return {str(v) for v in value}
