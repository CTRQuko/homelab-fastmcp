"""Centralised audit log for plugin tool calls.

Fire-and-forget: failures never bubble up to the caller. Each line is a JSON
object with timestamp, plugin, tool, arguments hash, duration and status, so
log rotation tools and external parsers can consume it uniformly.

Rotation: when the active log exceeds ``_MAX_LOG_SIZE`` bytes, it is
rotated to ``audit.log.1`` (older backups shifted outward, oldest beyond
``_BACKUP_COUNT`` deleted). This is best-effort and silent — never blocks
a tool call.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# Rotation tunables. Defaults: 10 MB per log file, 7 backups kept (~1
# week at moderate use). Operators can override via env if needed.
_MAX_LOG_SIZE = int(os.environ.get("MIMIR_AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))
_BACKUP_COUNT = int(os.environ.get("MIMIR_AUDIT_BACKUP_COUNT", "7"))

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


def _rotate_if_needed(target: Path) -> None:
    """Best-effort size-based rotation; never raises.

    Called inline by :func:`log_tool_call` before the append so the active
    file stays under :data:`_MAX_LOG_SIZE`. The previous version of this
    module promised "rotation is daily by date" in the docs but never
    implemented it — long-running operators saw the log grow indefinitely.
    """
    try:
        if target.stat().st_size < _MAX_LOG_SIZE:
            return
    except OSError:
        return
    try:
        # Drop the oldest backup if we are at the cap, then shift each
        # backup one slot outward (.6 → .7, .5 → .6, …, .1 → .2).
        oldest = target.with_name(f"{target.name}.{_BACKUP_COUNT}")
        if oldest.exists():
            try:
                oldest.unlink()
            except OSError:
                pass
        for i in range(_BACKUP_COUNT - 1, 0, -1):
            src = target.with_name(f"{target.name}.{i}")
            dst = target.with_name(f"{target.name}.{i + 1}")
            if src.exists():
                try:
                    src.replace(dst)
                except OSError:
                    pass
        # Move the active log into slot 1. The next log_tool_call recreates
        # the active file.
        try:
            target.replace(target.with_name(f"{target.name}.1"))
        except OSError:
            pass
    except OSError:
        return


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
            _rotate_if_needed(target)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Audit must never break the caller.
        return
