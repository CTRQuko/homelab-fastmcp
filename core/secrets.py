"""Scoped credential vault for the router.

Plugins never read raw environment variables or dotenv files. They request
credentials by reference (e.g. ``PROXMOX_PVE1_TOKEN``) through a
:class:`PluginContext` and the router validates whether the plugin's
manifest authorises the reference.

Resolution order for a credential value:

1. Process environment variable (``os.environ``)
2. ``<homelab_dir>/.config/secrets/*.md`` files (lines ``KEY=value``)
3. ``.env`` at the framework root (fallback)

Values are never logged. :func:`mask` returns a redacted form for logs.
"""
from __future__ import annotations

import fnmatch
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

_HOMELAB_DIR = os.environ.get("HOMELAB_DIR", "C:/homelab")
_SECRET_DIRS = [Path(_HOMELAB_DIR) / ".config/secrets"]
_PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


class CredentialAccessDenied(RuntimeError):
    """Raised when a plugin requests a credential outside its manifest scope."""


class CredentialNotFound(RuntimeError):
    """Raised when no source provides a value for the requested reference."""


@dataclass(frozen=True)
class PluginContext:
    """Scope information passed by the router when a plugin asks for a secret.

    ``credential_patterns`` are fnmatch-style globs declared in the plugin
    manifest under ``[security].credential_refs``. An empty list means the
    plugin may not read any credentials.
    """

    plugin_name: str
    credential_patterns: tuple[str, ...] = field(default_factory=tuple)

    def allows(self, ref: str) -> bool:
        return any(fnmatch.fnmatchcase(ref, pat) for pat in self.credential_patterns)


def _from_env(key: str) -> str | None:
    val = os.environ.get(key, "").strip()
    return val or None


def _from_md_files(key: str) -> str | None:
    """Scan ``<HOMELAB_DIR>/.config/secrets/*.md`` for a ``KEY=value`` line.

    The key must sit at column 0 (no leading whitespace) to avoid false
    positives from indented examples in fenced code blocks, markdown tables,
    or operator docs stored under the same directory.
    """
    found: str | None = None
    duplicates = 0
    prefix = f"{key}="
    for directory in _SECRET_DIRS:
        if not directory.exists():
            continue
        for file in directory.glob("*.md"):
            try:
                for raw in file.read_text(encoding="utf-8").splitlines():
                    if not raw.startswith(prefix):
                        continue
                    value = raw[len(prefix):].rstrip()
                    if found is None:
                        found = value
                    elif value != found:
                        duplicates += 1
            except OSError:
                continue
    if duplicates:
        warnings.warn(
            f"Secret '{key}' has {duplicates + 1} divergent definitions; "
            "using first match.",
            UserWarning,
            stacklevel=3,
        )
    return found


def _parse_env_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ('"', "'"):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else raw[1:]
    for sep in (" #", "\t#"):
        idx = raw.find(sep)
        if idx >= 0:
            raw = raw[:idx]
            break
    return raw.strip()


def _from_dotenv(key: str) -> str | None:
    if not _PROJECT_ENV.exists():
        return None
    try:
        for line in _PROJECT_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return _parse_env_value(line.split("=", 1)[1])
    except OSError:
        pass
    return None


def _resolve(key: str) -> str | None:
    return _from_env(key) or _from_md_files(key) or _from_dotenv(key)


def get_credential(ref: str, ctx: PluginContext) -> str:
    """Return the value for ``ref`` if the plugin context allows it."""
    if not ctx.allows(ref):
        raise CredentialAccessDenied(
            f"Plugin '{ctx.plugin_name}' is not authorised to read '{ref}'"
        )
    value = _resolve(ref)
    if not value:
        raise CredentialNotFound(
            f"No value for '{ref}' in env, secrets/*.md or .env"
        )
    return value


def has_credential(ref: str) -> bool:
    """Probe whether a credential ref resolves to a non-empty value.

    Scope-agnostic — used by the bootstrap tools to tell the LLM which
    credentials still need to be supplied by the user. Never returns the value.
    """
    return bool(_resolve(ref))


def mask(value: str, visible: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return value[:visible] + "****"
