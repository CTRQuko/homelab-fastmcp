"""Plugin lifecycle management for Mimir.

Four operations expressed as pure functions so the router can wrap them
as tools and tests can hit them directly:

- :func:`install_plugin`: take a source (``github:owner/repo``, https URL,
  or local path) and either emit a textual instruction for the operator
  to execute, or run the clone/copy directly when the operator has
  granted permission.
- :func:`remove_plugin`: mirror of install.
- :func:`set_plugin_enabled`: toggle the ``[plugin].enabled`` flag in a
  plugin's manifest.  Editing preserves the rest of the file via a
  targeted regex.
- :func:`list_plugins`: rich listing with manifest-level detail.

All four refuse to act on paths outside ``plugins_dir`` and validate the
plugin name shape so a malicious argument cannot escape the sandbox.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.loader import parse_manifest, ManifestError

# Directory names accept the full GitHub-repo alphabet (hyphens included)
# to match the way the rest of the ecosystem names its repos. The
# `[plugin].name` field inside the manifest stays snake_case; they are
# two distinct spaces.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_GITHUB_REF_RE = re.compile(r"^github:([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)$")
_ENABLED_LINE_RE = re.compile(r"^(\s*enabled\s*=\s*)(true|false)", re.MULTILINE)


class PluginMgmtError(ValueError):
    """Raised when a plugin management operation cannot proceed."""


# ---------------------------------------------------------------------------
# Source parsing
# ---------------------------------------------------------------------------


def parse_install_source(source: str) -> dict[str, Any]:
    """Normalise an install source string into an action spec.

    Supported forms:

    - ``github:owner/repo`` → ``git clone https://github.com/owner/repo``
    - An https(s) URL ending in ``.git`` → direct ``git clone``
    - An absolute local path → ``cp -r`` style copy

    Returns a dict with keys ``kind``, ``command`` (human-readable), and
    either ``url`` or ``source_path`` depending on the kind. Raises
    :class:`PluginMgmtError` on anything else.
    """
    source = (source or "").strip()
    if not source:
        raise PluginMgmtError("source must not be empty")

    gh = _GITHUB_REF_RE.match(source)
    if gh:
        owner, repo = gh.group(1), gh.group(2)
        # Default target name = the repo name minus a trailing "-mcp" if
        # present, so `github:acme/foo-mcp` becomes `plugins/foo/`.
        target = repo[:-4] if repo.lower().endswith("-mcp") else repo
        return {
            "kind": "git",
            "url": f"https://github.com/{owner}/{repo}.git",
            "target_name": target,
            "command": f"git clone https://github.com/{owner}/{repo}.git",
        }

    if source.startswith("http://") or source.startswith("https://"):
        parsed = urlparse(source)
        if not parsed.netloc or not parsed.path:
            raise PluginMgmtError(f"not a valid URL: {source!r}")
        last = parsed.path.rstrip("/").split("/")[-1]
        target = last[:-4] if last.endswith(".git") else last
        if target.lower().endswith("-mcp"):
            target = target[:-4]
        if not target:
            raise PluginMgmtError(f"cannot derive plugin name from URL: {source!r}")
        return {
            "kind": "git",
            "url": source,
            "target_name": target,
            "command": f"git clone {source}",
        }

    # Local path
    p = Path(source).expanduser()
    if not p.is_absolute():
        raise PluginMgmtError(
            f"local source must be an absolute path, got {source!r}"
        )
    if not p.exists():
        raise PluginMgmtError(f"local source does not exist: {p}")
    if not p.is_dir():
        raise PluginMgmtError(f"local source must be a directory: {p}")
    return {
        "kind": "copy",
        "source_path": str(p),
        "target_name": p.name,
        "command": f"cp -r {p}",
    }


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _validate_plugin_name(name: str) -> str:
    """Reject names outside the ``[a-z][a-z0-9_]*`` alphabet.

    Blocks path traversal attempts (``../``, ``/``) and anything that
    could not be a real plugin directory. The regex matches the schema
    documented in ``docs/naming-guide.md`` and what the loader enforces
    in ``parse_manifest``.
    """
    if not name or not _NAME_RE.match(name):
        raise PluginMgmtError(
            f"invalid plugin name {name!r}: must match ^[a-z][a-z0-9_-]*$"
        )
    return name


def _resolve_within(plugins_dir: Path, name: str) -> Path:
    """Return ``plugins_dir/<name>`` after verifying it does not escape.

    Even with ``_validate_plugin_name`` the defensive ``resolve()`` check
    catches symlink shenanigans where ``plugins_dir/<name>`` resolves to
    somewhere else on disk.
    """
    _validate_plugin_name(name)
    plugins_dir = plugins_dir.resolve()
    target = (plugins_dir / name).resolve()
    try:
        target.relative_to(plugins_dir)
    except ValueError as exc:
        raise PluginMgmtError(
            f"plugin path {target} escapes plugins_dir {plugins_dir}"
        ) from exc
    return target


# ---------------------------------------------------------------------------
# Install / remove
# ---------------------------------------------------------------------------


def install_plugin(
    source: str,
    plugins_dir: Path,
    *,
    execute: bool = False,
    name_override: str | None = None,
) -> dict[str, Any]:
    """Install a plugin from ``source`` under ``plugins_dir``.

    ``execute=False`` (default) returns an instruction payload so the LLM
    can hand the command to the operator. ``execute=True`` actually runs
    ``git clone`` or a local copy — only call with ``execute=True`` when
    the operator's ``router.toml`` has ``allow_plugin_install = true``.

    The target directory is validated to stay inside ``plugins_dir`` and
    must not already exist when executing.
    """
    spec = parse_install_source(source)
    raw_name = name_override or spec["target_name"]
    name = _validate_plugin_name(raw_name)
    target = _resolve_within(plugins_dir, name)

    full_command = f"{spec['command']} {target}"

    if not execute:
        return {
            "action": "manual_instruction",
            "source": source,
            "target_path": str(target),
            "target_name": name,
            "command": full_command,
            "hint": (
                "Run the command above on your machine. After it completes, "
                "restart Mimir so the new plugin is picked up."
            ),
            "executed": False,
        }

    # Permissive path — run the clone/copy ourselves.
    if target.exists():
        raise PluginMgmtError(
            f"target {target} already exists; remove it first or use a different name"
        )
    plugins_dir.mkdir(parents=True, exist_ok=True)

    if spec["kind"] == "git":
        try:
            subprocess.run(
                ["git", "clone", spec["url"], str(target)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError as exc:
            raise PluginMgmtError("git is not available on PATH") from exc
        except subprocess.CalledProcessError as exc:
            raise PluginMgmtError(
                f"git clone failed: {exc.stderr.strip() or exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PluginMgmtError("git clone timed out after 5 minutes") from exc
    else:  # copy
        shutil.copytree(spec["source_path"], target)

    return {
        "action": "installed",
        "source": source,
        "target_path": str(target),
        "target_name": name,
        "command": full_command,
        "hint": (
            "Restart Mimir so the new plugin is picked up by discovery."
        ),
        "executed": True,
    }


def remove_plugin(
    name: str, plugins_dir: Path, *, execute: bool = False
) -> dict[str, Any]:
    """Remove ``plugins_dir/<name>/``.

    Strict mode (``execute=False``) returns the ``rm -rf`` command the
    operator can run. Permissive mode (``execute=True``) calls
    :func:`shutil.rmtree` directly — only enabled when the config says so.
    """
    target = _resolve_within(plugins_dir, name)
    if not target.exists():
        raise PluginMgmtError(f"plugin {name!r} not found at {target}")
    if not target.is_dir():
        raise PluginMgmtError(f"plugin path {target} is not a directory")

    command = f"rm -rf {target}"
    if not execute:
        return {
            "action": "manual_instruction",
            "target_path": str(target),
            "target_name": name,
            "command": command,
            "hint": "Run the command above to remove the plugin.",
            "executed": False,
        }

    shutil.rmtree(target)
    return {
        "action": "removed",
        "target_path": str(target),
        "target_name": name,
        "command": command,
        "hint": "Restart Mimir so the plugin stops being mounted.",
        "executed": True,
    }


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


def set_plugin_enabled(
    name: str, plugins_dir: Path, *, enabled: bool
) -> dict[str, Any]:
    """Toggle the ``[plugin].enabled`` flag in ``plugins_dir/<name>/plugin.toml``.

    A targeted regex edit preserves the rest of the manifest (comments,
    blank lines, key order). If the flag is absent, it is appended
    after the ``[plugin]`` section header.

    Returns ``{"name", "previous", "current", "path"}``.
    """
    target = _resolve_within(plugins_dir, name)
    manifest_path = target / "plugin.toml"
    if not manifest_path.is_file():
        raise PluginMgmtError(f"{manifest_path} not found")

    # Validate by parsing — refuse to edit a broken manifest.
    try:
        manifest = parse_manifest(manifest_path, strict=True)
    except ManifestError as exc:
        raise PluginMgmtError(
            f"refusing to edit invalid manifest at {manifest_path}: {exc}"
        ) from exc

    previous = bool(manifest.enabled)
    new_val = "true" if enabled else "false"
    text = manifest_path.read_text(encoding="utf-8")

    new_text, n = _ENABLED_LINE_RE.subn(rf"\g<1>{new_val}", text, count=1)
    if n == 0:
        # No explicit 'enabled = …' in the file. Insert one right after
        # the [plugin] header so it lands in the right section even if
        # the plugin.toml is a minimal stub.
        header_re = re.compile(r"^(\[plugin\]\s*\n)", re.MULTILINE)
        match = header_re.search(text)
        if not match:
            raise PluginMgmtError(
                f"{manifest_path}: cannot locate [plugin] header to insert 'enabled ='"
            )
        insertion_point = match.end()
        new_text = (
            text[:insertion_point]
            + f"enabled = {new_val}\n"
            + text[insertion_point:]
        )

    manifest_path.write_text(new_text, encoding="utf-8")

    return {
        "name": name,
        "previous": previous,
        "current": enabled,
        "path": str(manifest_path),
        "hint": (
            "Restart Mimir so the change takes effect."
            if previous != enabled
            else "Flag already at the requested value; no restart needed."
        ),
    }


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


def _toml_str(value: str) -> str:
    """Render ``value`` as a TOML basic string (double-quoted, escaped)."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_str_list(values: list[str] | None) -> str:
    """Render ``values`` as a TOML inline array of basic strings."""
    if not values:
        return "[]"
    inner = ", ".join(_toml_str(str(v)) for v in values)
    return f"[{inner}]"


def scaffold_plugin(
    name: str,
    plugins_dir: Path,
    *,
    runtime_command: str,
    runtime_args: list[str] | None = None,
    credential_refs: list[str] | None = None,
    description: str | None = None,
    version: str = "0.1.0",
) -> dict[str, Any]:
    """Generate a minimal ``plugin.toml`` under ``plugins_dir/<name>/``.

    Validates ``name`` with the existing :func:`_validate_plugin_name`
    regex (anti-traversal, snake_case alphabet) and refuses if the
    target directory already exists. The manifest follows the
    canonical schema documented in :mod:`core.loader`:

    - ``[plugin]`` — name, version, enabled=true, optional description.
    - ``[runtime]`` — ``command`` + optional ``args``.
    - ``[security]`` — ``credential_refs`` (the only field actively
      enforced today). Other fields (``inventory_access``,
      ``network_dynamic``, ``filesystem_*``, ``exec``) are emitted as
      permissive defaults with a disclaimer comment so plugin
      authors do not mistake them for a sandbox.
    - ``[tools]`` — empty whitelist/blacklist (allow everything the
      subprocess defines).

    Does **not** create ``server.py``: plugin authors or upstream
    MCP repos provide the entry point. After scaffolding, drop the
    plugin code into ``plugins_dir/<name>/`` and restart Mimir to
    pick it up.

    Returns a dict with ``name``, ``path`` (the directory), and
    ``manifest_path`` (the file written).
    """
    target = _resolve_within(plugins_dir, name)
    if target.exists():
        raise PluginMgmtError(
            f"plugin directory {target} already exists; refusing to overwrite"
        )

    if not isinstance(runtime_command, str) or not runtime_command.strip():
        raise PluginMgmtError("runtime_command must be a non-empty string")

    args_block = _toml_str_list(runtime_args)
    cred_refs_block = _toml_str_list(credential_refs)
    desc_line = (
        f"description = {_toml_str(description)}\n"
        if description
        else ""
    )

    manifest = (
        f"# Generated by router_scaffold_plugin. Drop your code into this\n"
        f"# directory (server.py, or whatever the runtime command\n"
        f"# launches) and restart Mimir to discover the plugin.\n"
        f"\n"
        f"[plugin]\n"
        f"name = {_toml_str(name)}\n"
        f"version = {_toml_str(version)}\n"
        f"enabled = true\n"
        f"{desc_line}"
        f"\n"
        f"[runtime]\n"
        f"command = {_toml_str(runtime_command)}\n"
        f"args = {args_block}\n"
        f"\n"
        f"[security]\n"
        f"# ENFORCED today: credential_refs.\n"
        f"credential_refs = {cred_refs_block}\n"
        f"\n"
        f"# Reserved for future enforcement (Layer 5 — see\n"
        f"# docs/security-model.md). Parsed but NOT enforced today;\n"
        f"# treat them as documentation of intent, not a sandbox.\n"
        f"inventory_access = []\n"
        f"network_dynamic = false\n"
        f"filesystem_read = []\n"
        f"filesystem_write = []\n"
        f"exec = []\n"
        f"\n"
        f"[tools]\n"
        f"# Empty whitelist + empty blacklist = expose everything\n"
        f"# the subprocess defines. See docs/security-model.md.\n"
        f"whitelist = []\n"
        f"blacklist = []\n"
    )

    target.mkdir(parents=True, exist_ok=False)
    manifest_path = target / "plugin.toml"
    manifest_path.write_text(manifest, encoding="utf-8")

    return {
        "name": name,
        "path": str(target),
        "manifest_path": str(manifest_path),
        "hint": (
            "Drop your plugin code into the directory above (entry "
            "matching the runtime command/args you provided) and "
            "restart Mimir to discover the plugin."
        ),
    }


def list_plugins(plugins_dir: Path) -> list[dict[str, Any]]:
    """Return one entry per ``plugins_dir/*/plugin.toml`` found.

    Parse failures surface as an entry with ``status = "quarantined"``
    rather than raising — the operator wants to see what broke.
    """
    if not plugins_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "plugin.toml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = parse_manifest(manifest_path, strict=True)
            out.append(
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "enabled": bool(manifest.enabled),
                    "path": str(entry),
                    "status": "ok",
                }
            )
        except ManifestError as exc:
            out.append(
                {
                    "name": entry.name,
                    "version": None,
                    "enabled": None,
                    "path": str(entry),
                    "status": "quarantined",
                    "error": str(exc),
                }
            )
    return out
