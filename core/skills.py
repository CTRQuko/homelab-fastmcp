"""Skills and agents discovery.

Scans ``skills_dir`` and ``agents_dir`` (paths configured in
``router.toml``) for ``.md`` files with valid YAML frontmatter and exposes
each one as a router tool.

The contract is minimal:

- Frontmatter is a ``---``-delimited YAML block at the top of the file.
- ``name`` and ``description`` are required fields.
- Content after the frontmatter is the skill body.

Agents are the same format, addressed under a separate root so the router
can tell them apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9_]")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path
    kind: str  # "skill" | "agent"


def _parse_frontmatter(text: str) -> tuple[dict, str] | None:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    return meta, match.group(2)


def _tool_safe_name(raw: str) -> str:
    """Turn a skill name into something legal as an MCP tool identifier."""
    slug = _NAME_SANITIZE_RE.sub("_", raw.strip().lower())
    return slug.strip("_") or "unnamed"


def _scan_dir(directory: Path, kind: str) -> list[Skill]:
    if not directory.exists() or not directory.is_dir():
        return []
    out: list[Skill] = []
    for path in sorted(directory.rglob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = _parse_frontmatter(raw)
        if parsed is None:
            continue
        meta, body = parsed
        name = meta.get("name")
        description = meta.get("description")
        if not name or not description:
            continue
        out.append(
            Skill(
                name=_tool_safe_name(str(name)),
                description=str(description),
                body=body,
                path=path,
                kind=kind,
            )
        )
    # Deduplicate by name (last wins — users can override bundled skills).
    seen: dict[str, Skill] = {}
    for skill in out:
        seen[skill.name] = skill
    return list(seen.values())


def discover_skills(skills_dir: Path | None) -> list[Skill]:
    if skills_dir is None:
        return []
    return _scan_dir(skills_dir, "skill")


def discover_agents(agents_dir: Path | None) -> list[Skill]:
    if agents_dir is None:
        return []
    return _scan_dir(agents_dir, "agent")
