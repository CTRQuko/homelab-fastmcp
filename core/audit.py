"""Centralised audit log for plugin tool calls.

Fire-and-forget: failures never bubble up to the caller. Each line is a JSON
object with timestamp, plugin, tool, arguments hash, duration and status, so
log rotation tools and external parsers can consume it uniformly.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

def _resolve_default_audit_path() -> Path:
    """Pick the audit log path with backward-compat for the old name.

    ``MIMIR_AUDIT_LOG`` is the canonical override from v0.1.0. The
    ``HOMELAB_FASTMCP_AUDIT_LOG`` name from earlier prototypes is still
    honoured but emits a DeprecationWarning so operators know to rename.
    Falls back to ``<framework_root>/config/audit.log`` when neither is
    set — same default as before.
    """
    explicit = os.environ.get("MIMIR_AUDIT_LOG")
    if explicit:
        return Path(explicit)
    legacy = os.environ.get("HOMELAB_FASTMCP_AUDIT_LOG")
    if legacy:
        import warnings as _warnings
        _warnings.warn(
            "HOMELAB_FASTMCP_AUDIT_LOG is deprecated; rename to MIMIR_AUDIT_LOG",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(legacy)
    return Path(__file__).resolve().parent.parent / "config" / "audit.log"


_DEFAULT_PATH = _resolve_default_audit_path()

_lock = threading.Lock()


def _hash_args(args: Any) -> str:
    try:
        payload = json.dumps(args, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        payload = repr(args).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def log_tool_call(
    plugin: str,
    tool: str,
    args: Any,
    duration_ms: float,
    status: str,
    path: Path | None = None,
) -> None:
    """Append one audit entry. Never raises."""
    target = path or _DEFAULT_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "plugin": plugin,
            "tool": tool,
            "args_hash": _hash_args(args),
            "duration_ms": round(duration_ms, 2),
            "status": status,
        }
        with _lock:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Audit must never break the caller.
        return
