"""Profile loader — gates which discovered plugins actually activate.

``profiles/<name>.yaml`` looks like::

    enabled_plugins:
      - proxmox
      - github

Semantics:

- Missing file OR missing ``enabled_plugins`` key: ``None`` is returned and
  the router treats every discovered plugin as enabled.
- Empty list (``[]``): no plugins activate — only core + meta-tools are
  exposed. This is the default shipped in ``profiles/default.yaml``.
- Non-empty list: only plugins whose manifest name appears here activate;
  everything else is marked ``disabled_by_profile``.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_enabled_plugins(profile_path: Path) -> set[str] | None:
    """Return the set of plugin names the profile allows, or ``None``.

    ``None`` means "no gate configured — admit everything".
    """
    if not profile_path.exists():
        return None
    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    if "enabled_plugins" not in data:
        return None
    value = data.get("enabled_plugins")
    if value is None:
        return set()
    if not isinstance(value, list):
        return None
    return {str(v) for v in value}
