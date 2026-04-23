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

_DEFAULT_PATH = Path(
    os.environ.get(
        "HOMELAB_FASTMCP_AUDIT_LOG",
        str(Path(__file__).resolve().parent.parent / "config" / "audit.log"),
    )
)

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
