"""Bootstrap meta-tools for self-onboarding.

When the framework starts with an empty inventory or plugins with unmet
``[requires]``, these tools expose a guided path so an LLM can walk the
user through filling in what's missing.

They are pure functions; the router is responsible for registering them
as MCP tools. Each returns JSON-serialisable dicts so the LLM sees a
structured payload (not shell stdout).

Credential writes are intentionally constrained:

- Values go to ``<MIMIR_HOME>/secrets/router_vault.md``
- The credential_ref must match at least one plugin's declared pattern,
  otherwise the write is rejected to avoid the user dumping arbitrary
  secrets into the file.
- The file is created with mode 0o600 on POSIX (best-effort on Windows).
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from core.inventory import (
    _VALID_HOST_TYPES,
    Inventory,
    append_host,
    append_service,
)
from core.loader import LoadReport

_VAULT_FILENAME = "router_vault.md"
_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` atomically (write + fsync + rename).

    Truncating ``path`` directly with ``write_text`` left it half-written if
    the process died mid-flush. Writing to a sibling tempfile and renaming
    is atomic on POSIX and near-atomic on Windows (``os.replace``).
    """
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=".swap"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync may not be supported on every FS (e.g. some SMB
                # mounts). Best-effort; the rename below is what gives the
                # atomic guarantee.
                pass
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def router_status(
    inventory: Inventory,
    report: LoadReport,
    memory_backend: str,
) -> dict[str, Any]:
    inv = inventory.summary()
    return {
        "framework": "mimir",
        "memory_backend": memory_backend,
        "inventory": inv,
        "plugins": [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "status": p.status,
                "missing": [
                    {"kind": m.kind, "detail": m.detail, "prompt": m.prompt}
                    for m in p.missing
                ],
            }
            for p in report.plugins
        ],
        "setup_pending": [
            p.manifest.name for p in report.plugins if p.status == "pending_setup"
        ],
    }


def router_help() -> dict[str, Any]:
    return {
        "name": "mimir",
        "purpose": (
            "Mimir is a declarative MCP router. Users describe their "
            "infrastructure in inventory/*.yaml and drop plugins under "
            "plugins/. The router discovers them, wires them up, exposes "
            "each plugin's tools, and guides the LLM through the missing "
            "pieces."
        ),
        "next_steps": [
            "Call router_status() to see the current state.",
            "If no hosts are declared, call router_add_host() for each machine "
            "you want the framework to manage.",
            "For each plugin in 'pending_setup', call setup_<plugin_name>() to "
            "learn what inputs it needs.",
        ],
        "available_bootstrap_tools": [
            "router_status",
            "router_help",
            "router_add_host",
            "router_add_service",
            "router_add_credential",
        ],
    }


def router_add_host(
    inventory_dir: Path,
    name: str,
    type: str,
    address: str,
    port: int | None = None,
    credential_ref: str | None = None,
    auth_method: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    # Validate the type BEFORE persisting. Otherwise an invalid type lands
    # in hosts.yaml and the next router boot crashes (loader rejects the
    # whole inventory because the loaded host fails type check). Concrete
    # repro: 2026-05-06 added a host with type='ubiquiti-switch' and the
    # next mimir restart failed to load until manual yaml fix.
    if type not in _VALID_HOST_TYPES:
        return {
            "ok": False,
            "error": (
                f"type '{type}' not in valid host types: "
                f"{sorted(_VALID_HOST_TYPES)}. Pick one of these or extend "
                "core/inventory.py:_VALID_HOST_TYPES if a new category is "
                "genuinely needed (and add a tag like 'ubiquiti', 'router', "
                "'switch' to disambiguate within 'network-device')."
            ),
        }
    host: dict[str, Any] = {"name": name, "type": type, "address": address}
    if port is not None:
        host["port"] = int(port)
    if credential_ref or auth_method:
        host["auth"] = {
            "method": auth_method or "ssh_key",
            "credential_ref": credential_ref,
        }
    if tags:
        host["tags"] = list(tags)
    append_host(inventory_dir, host)
    return {"ok": True, "added": host}


def router_add_service(
    inventory_dir: Path,
    name: str,
    type: str,
    host_ref: str,
    port: int | None = None,
    credential_ref: str | None = None,
    auth_method: str | None = None,
) -> dict[str, Any]:
    service: dict[str, Any] = {"name": name, "type": type, "host_ref": host_ref}
    if port is not None:
        service["port"] = int(port)
    if credential_ref or auth_method:
        service["auth"] = {
            "method": auth_method or "token",
            "credential_ref": credential_ref,
        }
    append_service(inventory_dir, service)
    return {"ok": True, "added": service}


def _allowed_patterns_from_report(report: LoadReport) -> list[str]:
    """Collect credential_refs declared by *enabled* plugins' manifests.

    Disabled and errored plugins are skipped so a dropped-in malicious
    ``plugin.toml`` cannot widen the allowlist without first being enabled.
    Plugins in ``pending_setup`` remain eligible — that is exactly when the
    user is expected to supply their first credential.
    """
    patterns: list[str] = []
    for state in report.plugins:
        if not state.manifest.enabled or state.status in {"disabled", "error"}:
            continue
        sec = state.manifest.security or {}
        for ref in sec.get("credential_refs", []) or []:
            patterns.append(str(ref))
    return patterns


def router_add_credential(
    ref: str,
    value: str,
    report: LoadReport,
    vault_dir: Path | None = None,
) -> dict[str, Any]:
    """Write a credential to the scoped vault.

    The ``ref`` must match one of the ``credential_refs`` patterns declared
    by a loaded plugin manifest; otherwise the write is rejected. The raw
    value is never echoed back — only the reference and a masked preview.
    """
    if not _REF_RE.match(ref):
        return {
            "ok": False,
            "error": "ref must be UPPER_SNAKE_CASE, 3-64 chars, starting with a letter",
        }
    # Reject control chars in value to prevent newline-injected extra entries
    # in the vault file (an attacker with one ref could otherwise insert a
    # second key/value and escape scope).
    if any(c in value for c in ("\n", "\r", "\x00")):
        return {
            "ok": False,
            "error": "value must not contain newline or NUL characters",
        }
    allowed = _allowed_patterns_from_report(report)
    import fnmatch

    if not any(fnmatch.fnmatchcase(ref, pat) for pat in allowed):
        return {
            "ok": False,
            "error": (
                f"No loaded plugin declares a credential pattern that matches '{ref}'. "
                "Install the plugin first, then retry."
            ),
            "allowed_patterns": allowed,
        }

    if vault_dir is None:
        # Use the same resolution order the secrets module uses so the
        # write path lands exactly where the read path looks.
        from core.secrets import resolve_home

        vault_dir = resolve_home() / "secrets"
    vault_dir.mkdir(parents=True, exist_ok=True)
    vault_file = vault_dir / _VAULT_FILENAME

    existing_lines: list[str] = []
    if vault_file.exists():
        existing_lines = [
            line
            for line in vault_file.read_text(encoding="utf-8").splitlines()
            if not line.startswith(f"{ref}=")
        ]
    existing_lines.append(f"{ref}={value}")
    # Atomic write keeps the vault file consistent if the process dies
    # mid-flush; previously a crash between truncate and close left the
    # vault empty or half-written.
    _atomic_write_text(
        vault_file,
        "\n".join(existing_lines) + "\n",
        mode=0o600,
    )

    # Best-effort mirror to the OS keyring. If the backend is available
    # the next read of `ref` returns the keyring value before falling
    # back to the vault file. If it fails (no backend, headless), the
    # vault file remains the source of truth — operator notices nothing.
    from core.secrets import set_keyring  # lazy: avoid module-load cycles
    keyring_ok = set_keyring(ref, value)

    preview = (value[:4] + "****") if len(value) > 8 else "*" * len(value)
    return {
        "ok": True,
        "ref": ref,
        "preview": preview,
        "file": str(vault_file),
        "keyring": keyring_ok,
    }
