"""Scoped credential vault for the router.

Plugins never read raw environment variables or dotenv files. They request
credentials by reference (e.g. ``PROXMOX_PVE1_TOKEN``) through a
:class:`PluginContext` and the router validates whether the plugin's
manifest authorises the reference.

Resolution order for a credential value:

1. Process environment variable (``os.environ``)
2. ``<MIMIR_HOME>/secrets/*.md`` files (lines ``KEY=value``;
   ``<MIMIR_HOME>/.config/secrets/*.md`` is also scanned for legacy
   layouts)
3. ``.env`` at the framework root (fallback)

Values are never logged. :func:`mask` returns a redacted form for logs.
"""
from __future__ import annotations

import fnmatch
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

def _platform_default_home() -> Path:
    """Return the default Mimir home dir when no env var is set.

    Windows → ``%APPDATA%/mimir`` (or ``~/AppData/Roaming/mimir`` if
    APPDATA is unset). Everything else → ``$XDG_CONFIG_HOME/mimir`` (or
    ``~/.config/mimir`` when XDG_CONFIG_HOME is unset). Picks platform-
    standard config locations so a clean install doesn't litter the
    user's home with framework state.
    """
    import sys
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "mimir"
        return Path.home() / "AppData" / "Roaming" / "mimir"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "mimir"
    return Path.home() / ".config" / "mimir"


def resolve_home() -> Path:
    """Where Mimir reads vault files and operator-supplied state from.

    Resolution order:

    1. ``MIMIR_HOME`` — the canonical name from v0.1.0 onwards.
    2. ``HOMELAB_DIR`` — legacy name kept so operators carrying it over
       from before the rename don't get a silent break. Reading it
       emits a one-shot DeprecationWarning so they know to migrate.
    3. Platform default (see :func:`_platform_default_home`).
    """
    explicit = os.environ.get("MIMIR_HOME")
    if explicit:
        return Path(explicit).expanduser()
    legacy = os.environ.get("HOMELAB_DIR")
    if legacy:
        warnings.warn(
            "HOMELAB_DIR is deprecated; rename to MIMIR_HOME",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(legacy).expanduser()
    return _platform_default_home()


# Computed lazily so tests can monkeypatch _SECRET_DIRS without having
# to reach inside resolve_home. The default value matches the canonical
# layout: <home>/.config/secrets/ on legacy installs, <home>/secrets/
# under the platform default.
_HOMELAB_DIR = str(resolve_home())  # kept as legacy alias for backward-compat
_SECRET_DIRS = [
    resolve_home() / "secrets",
    resolve_home() / ".config" / "secrets",  # legacy layout (homelab-style)
]
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
    """Scan ``<MIMIR_HOME>/secrets/*.md`` for a ``KEY=value`` line.

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


# ---------------------------------------------------------------------------
# Enumeration helpers (for subprocess plugin env scoping)
# ---------------------------------------------------------------------------
# A "credential-looking" key is an uppercase alnum+underscore identifier of
# at least 3 chars. Same shape the write path enforces in
# ``router_add_credential``. Kept strict on purpose: `PATH`, `HOME`,
# `APPDATA` etc. do not match (too short / no underscore), so generic
# system env vars are never classified as credentials.
_CRED_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _is_credential_key(key: str) -> bool:
    return bool(_CRED_KEY_RE.fullmatch(key)) and "_" in key


def list_candidate_refs() -> list[str]:
    """Return every credential-shaped key known to any resolution source.

    Scans ``os.environ`` + ``secrets/*.md`` + ``.env``. The caller pairs
    this list against a plugin's ``credential_refs`` patterns to decide
    which keys to forward to the plugin's subprocess. Values are never
    read here — the router calls :func:`_resolve` separately for the
    matching subset.
    """
    keys: set[str] = {k for k in os.environ if _is_credential_key(k)}

    for directory in _SECRET_DIRS:
        if not directory.exists():
            continue
        for file in directory.glob("*.md"):
            try:
                for raw in file.read_text(encoding="utf-8").splitlines():
                    if "=" not in raw:
                        continue
                    key = raw.split("=", 1)[0]
                    if _is_credential_key(key):
                        keys.add(key)
            except OSError:
                continue

    if _PROJECT_ENV.exists():
        try:
            for raw in _PROJECT_ENV.read_text(encoding="utf-8").splitlines():
                stripped = raw.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key = stripped.split("=", 1)[0].strip()
                if _is_credential_key(key):
                    keys.add(key)
        except OSError:
            pass

    return sorted(keys)


def resolve_refs_matching(patterns: list[str]) -> dict[str, str]:
    """Return a ``{key: value}`` dict for every candidate ref that matches
    at least one of the fnmatch-style ``patterns``.

    Missing values drop out silently — the router uses this to build a
    subprocess env dict, and an unresolved ref is not worth crashing
    startup over. The hard dependency channel is ``[requires]``, not this.
    """
    if not patterns:
        return {}
    out: dict[str, str] = {}
    for key in list_candidate_refs():
        if any(fnmatch.fnmatchcase(key, pat) for pat in patterns):
            value = _resolve(key)
            if value:
                out[key] = value
    return out
