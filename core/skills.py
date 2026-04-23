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

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9_]")

# Directories that never contain user skills but may be accidentally placed
# under skills_dir (dev clones, python/node caches). Skipping them bounds
# the cost of discovery on misconfigured layouts.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }
)

# rglob had no depth cap. A maliciously or accidentally deep tree would
# stall startup. 5 covers any reasonable skills layout.
_MAX_DEPTH = 5


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


def _iter_markdown(root: Path) -> list[Path]:
    """Walk ``root`` collecting ``*.md`` files, skipping noisy dirs and
    stopping at ``_MAX_DEPTH`` to bound the cost on misconfigured layouts."""
    found: list[Path] = []

    def walk(current: Path, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        try:
            entries = sorted(current.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                        continue
                    walk(entry, depth + 1)
                elif entry.is_file() and entry.suffix == ".md":
                    found.append(entry)
            except OSError:
                continue

    walk(root, 0)
    return found


def _scan_dir(directory: Path, kind: str) -> list[Skill]:
    if not directory.exists() or not directory.is_dir():
        return []
    out: list[Skill] = []
    for path in _iter_markdown(directory):
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
    # A silent collision used to hide skills; now we log a warning so the
    # operator can spot unintended overrides at startup.
    seen: dict[str, Skill] = {}
    for skill in out:
        existing = seen.get(skill.name)
        if existing is not None and existing.path != skill.path:
            _log.warning(
                "%s name collision on '%s': kept %s, dropped %s",
                kind,
                skill.name,
                skill.path,
                existing.path,
            )
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
