"""Plugin manifest parser and state reconciler.

The loader walks ``plugins/`` looking for ``plugin.toml`` files, validates
them and produces a :class:`LoadReport` that the router prints at startup.
No subprocesses are spawned and no imports happen here; mounting plugins is
the caller's responsibility once reconciliation is accepted.

The format is:

.. code-block:: toml

   [plugin]
   name = "proxmox"
   version = "1.0.0"
   enabled = true

   [runtime]
   entry = "server.py"
   python = ">=3.11"
   deps = ["proxmoxer>=2.0.0"]
   venv = "auto"

   [security]
   inventory_access = ["hosts:type=proxmox"]
   credential_refs = ["PROXMOX_*_TOKEN"]
   network_dynamic = true
   filesystem_read = []
   filesystem_write = []
   exec = []

   [requires]
   hosts = [
     { type = "proxmox", min = 1, prompt = "Need a Proxmox node with API token" }
   ]
   credentials = [
     { pattern = "PROXMOX_*_TOKEN", prompt = "API token with VM.Audit + VM.PowerMgmt" }
   ]

   [tools]
   whitelist = []
   blacklist = []
"""
from __future__ import annotations

import fnmatch
import json
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.inventory import Inventory
from core.secrets import has_credential


class ManifestError(ValueError):
    """Raised when a ``plugin.toml`` is missing fields or malformed."""


@dataclass(frozen=True)
class Requirement:
    kind: str  # "hosts" | "credentials"
    detail: dict[str, Any]
    prompt: str


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    enabled: bool
    path: Path
    runtime: dict[str, Any]
    security: dict[str, Any]
    requires: list[Requirement]
    tools: dict[str, Any]


@dataclass
class PluginState:
    manifest: PluginManifest
    status: str  # "ok" | "pending_setup" | "disabled" | "error" | "quarantined"
    missing: list[Requirement] = field(default_factory=list)
    error: str | None = None


@dataclass
class QuarantineEntry:
    """A plugin directory whose manifest could not be parsed."""

    path: Path
    error: str


@dataclass
class LoadReport:
    plugins: list[PluginState]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    quarantined: list[QuarantineEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugins": [
                {
                    "name": p.manifest.name,
                    "version": p.manifest.version,
                    "status": p.status,
                    "path": str(p.manifest.path),
                    "missing": [asdict(m) for m in p.missing],
                    "error": p.error,
                }
                for p in self.plugins
            ],
            "added": self.added,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "quarantined": [
                {"path": str(q.path), "error": q.error} for q in self.quarantined
            ],
        }


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def _require_section(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    if key not in data:
        raise ManifestError(f"{path}: missing [{key}] section")
    section = data[key]
    if not isinstance(section, dict):
        raise ManifestError(f"{path}: [{key}] must be a table")
    return section


def parse_manifest(path: Path, strict: bool = True) -> PluginManifest:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: invalid TOML: {exc}") from exc

    plugin = _require_section(data, "plugin", path)
    for field_name in ("name", "version"):
        if field_name not in plugin:
            raise ManifestError(f"{path}: [plugin] missing '{field_name}'")

    if strict and "security" not in data:
        raise ManifestError(f"{path}: [security] is required when strict_manifest=true")

    requires_raw = data.get("requires", {}) or {}
    requires: list[Requirement] = []
    for item in requires_raw.get("hosts", []) or []:
        if not isinstance(item, dict) or "type" not in item:
            raise ManifestError(f"{path}: [requires.hosts] entries need a 'type'")
        detail: dict[str, Any] = {
            "type": item["type"],
            "min": int(item.get("min", 1)),
        }
        if "tag" in item:
            detail["tag"] = str(item["tag"])
        requires.append(
            Requirement(
                kind="hosts",
                detail=detail,
                prompt=str(item.get("prompt", "")),
            )
        )
    for item in requires_raw.get("credentials", []) or []:
        if not isinstance(item, dict) or "pattern" not in item:
            raise ManifestError(f"{path}: [requires.credentials] entries need a 'pattern'")
        requires.append(
            Requirement(
                kind="credentials",
                detail={"pattern": item["pattern"]},
                prompt=str(item.get("prompt", "")),
            )
        )

    return PluginManifest(
        name=str(plugin["name"]),
        version=str(plugin["version"]),
        enabled=bool(plugin.get("enabled", True)),
        path=path.parent.resolve(),
        runtime=data.get("runtime", {}) or {},
        security=data.get("security", {}) or {},
        requires=requires,
        tools=data.get("tools", {}) or {},
    )


# ---------------------------------------------------------------------------
# Requirement evaluation
# ---------------------------------------------------------------------------


def _check_requirement(req: Requirement, inventory: Inventory) -> bool:
    if req.kind == "hosts":
        matches = inventory.get_hosts(
            type=req.detail.get("type"),
            tag=req.detail.get("tag"),
        )
        return len(matches) >= int(req.detail.get("min", 1))
    if req.kind == "credentials":
        pattern = req.detail.get("pattern", "")
        if not pattern:
            return False
        # Literal ref (no glob) -> probe directly.
        if "*" not in pattern and "?" not in pattern:
            return has_credential(pattern)
        # Glob pattern -> accept if any env var matches AND resolves.
        for name in os.environ:
            if fnmatch.fnmatchcase(name, pattern) and has_credential(name):
                return True
        return False
    return False


def evaluate_plugin(manifest: PluginManifest, inventory: Inventory) -> PluginState:
    if not manifest.enabled:
        return PluginState(manifest=manifest, status="disabled")
    missing = [r for r in manifest.requires if not _check_requirement(r, inventory)]
    status = "ok" if not missing else "pending_setup"
    return PluginState(manifest=manifest, status=status, missing=missing)


# ---------------------------------------------------------------------------
# Discovery and reconciliation
# ---------------------------------------------------------------------------


def discover_manifests(
    plugin_dir: Path, strict: bool = True
) -> tuple[list[PluginManifest], list[QuarantineEntry]]:
    """Walk ``plugin_dir`` and split results into valid manifests + quarantine.

    A malformed ``plugin.toml`` used to abort the whole discovery; now it is
    isolated so one bad plugin cannot hide healthy siblings from the router.
    """
    if not plugin_dir.exists():
        return [], []
    manifests: list[PluginManifest] = []
    quarantined: list[QuarantineEntry] = []
    for candidate in sorted(plugin_dir.iterdir()):
        if not candidate.is_dir() or candidate.name.startswith((".", "_")):
            continue
        manifest_path = candidate / "plugin.toml"
        if not manifest_path.exists():
            continue
        try:
            manifests.append(parse_manifest(manifest_path, strict=strict))
        except ManifestError as exc:
            quarantined.append(QuarantineEntry(path=candidate, error=str(exc)))
    return manifests, quarantined


def reconcile(
    plugin_dir: Path,
    inventory: Inventory,
    state_path: Path,
    strict: bool = True,
) -> LoadReport:
    manifests, quarantined = discover_manifests(plugin_dir, strict=strict)
    states = [evaluate_plugin(m, inventory) for m in manifests]
    current = {s.manifest.name for s in states}
    previous = _read_state(state_path)
    report = LoadReport(
        plugins=states,
        added=sorted(current - previous),
        removed=sorted(previous - current),
        unchanged=sorted(current & previous),
        quarantined=quarantined,
    )
    _write_state(state_path, current)
    return report


def _read_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("plugins", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _write_state(path: Path, plugins: set[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"plugins": sorted(plugins)}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


# ---------------------------------------------------------------------------
# Helpers for bootstrap tools
# ---------------------------------------------------------------------------


def match_requirement_to_patterns(patterns: list[str], ref: str) -> bool:
    """Utility the router uses when scoping ``router_add_credential`` refs."""
    return any(fnmatch.fnmatchcase(ref, pat) for pat in patterns)


def tool_allowed(tools_cfg: dict[str, Any], tool_name: str) -> bool:
    """Check a tool name against a plugin's ``[tools]`` section.

    - ``blacklist``: if the tool matches any pattern here it is denied.
    - ``whitelist``: if set and non-empty, the tool must match at least one
      pattern. An empty/absent whitelist means "allow all not blacklisted".
    """
    blacklist = tools_cfg.get("blacklist") or []
    for pat in blacklist:
        if fnmatch.fnmatchcase(tool_name, str(pat)):
            return False
    whitelist = tools_cfg.get("whitelist") or []
    if not whitelist:
        return True
    return any(fnmatch.fnmatchcase(tool_name, str(pat)) for pat in whitelist)
