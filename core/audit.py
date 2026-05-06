"""Centralised audit log for plugin tool calls.

Fire-and-forget: failures never bubble up to the caller. Each line is a JSON
object with timestamp, plugin, tool, arguments hash, duration and status, so
log rotation tools and external parsers can consume it uniformly.

For entries with ``status != "ok"`` the payload is enriched with
``error_message`` (truncated 500 chars) and ``args_sanitized`` (dict/list
copy of the raw args with secret-shaped keys redacted). This makes the
audit log usable as input to ``scripts/audit-to-runtime-issues.py`` which
generates skeleton entries in ``docs/operator-notes/runtime-issues.md``
for the operator to complete. The richer payload only fires on errors so
the OK path stays cheap.

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


# Substrings (case-insensitive) that, when found in a key name, mark the
# corresponding value as a secret. Conservative side: prefer to redact a
# legitimate field than leak a credential. The list intentionally avoids
# generic words ("user", "name") that would over-redact useful diagnostic
# context.
#
# Note: `KEY` alone is NOT in the list — it matches `ok_key`, `monkey`,
# `key_id` and other false positives. We rely on compound forms
# (`API_KEY`, `APIKEY`, `ACCESS_KEY`, `SECRET_KEY`) that are intentional
# in real credential field names.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD",
    "AUTH", "BEARER", "CREDENTIAL", "COOKIE",
    "SESSION", "PRIVATE",
    "API_KEY", "APIKEY", "ACCESS_KEY", "SECRET_KEY", "PRIVATE_KEY",
)
# Cap on string values that are NOT redacted — keeps audit lines small
# and avoids dumping a 5MB stdout into the log.
_VALUE_TRUNCATE_LIMIT = 200


def _is_secret_key(key: Any) -> bool:
    return any(p in str(key).upper() for p in _SECRET_KEY_PATTERNS)


def _sanitize_args(args: Any) -> Any:
    """Best-effort redaction + truncation for audit error logs.

    Walks dicts/lists recursively. Replaces values of secret-shaped keys
    with ``<redacted>``. Truncates long strings to keep audit entries
    bounded. Non-JSON-friendly objects are stringified via ``repr()`` so
    the downstream ``json.dumps`` cannot fail on them.

    Never raises — falls back to ``"<sanitization_failed>"`` upstream.
    """
    if isinstance(args, dict):
        return {k: _sanitize_value(k, v) for k, v in args.items()}
    if isinstance(args, (list, tuple)):
        return [_sanitize_args(v) for v in args]
    if isinstance(args, str):
        if len(args) > _VALUE_TRUNCATE_LIMIT:
            return args[:_VALUE_TRUNCATE_LIMIT] + "...<truncated>"
        return args
    if isinstance(args, (int, float, bool)) or args is None:
        return args
    # Custom objects: stringify so JSON write doesn't crash. Truncated.
    s = repr(args)
    if len(s) > _VALUE_TRUNCATE_LIMIT:
        return s[:_VALUE_TRUNCATE_LIMIT] + "...<truncated>"
    return s


def _sanitize_value(key: Any, value: Any) -> Any:
    if _is_secret_key(key):
        return "<redacted>"
    return _sanitize_args(value)


def _resolve_client_id() -> str:
    """Identify the upstream MCP client invoking mimir.

    Order:
      1. ``MIMIR_CLIENT_ID`` env var (cliente lo pone al spawn).
      2. Process-tree parent name vía ``psutil`` (opcional, dependencia
         no obligatoria — fallback silencioso si no está instalada).
      3. ``"unknown"``.

    Never raises.
    """
    explicit = os.environ.get("MIMIR_CLIENT_ID", "").strip()
    if explicit:
        return explicit
    try:
        import psutil  # type: ignore[import-not-found]
        parent = psutil.Process().parent()
        if parent is not None:
            name = parent.name() or ""
            if name:
                return name
    except Exception:
        # ImportError, AccessDenied, NoSuchProcess, anything else — quiet.
        pass
    return "unknown"


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
    *,
    error_message: str | None = None,
    client: str | None = None,
) -> None:
    """Append one audit entry. Never raises.

    On error entries (``status != "ok"``) the payload includes:
      - ``error_message``: human-readable text from the catcher (truncated 500).
      - ``args_sanitized``: redacted/truncated copy of ``args`` for diagnosis.

    Both fields are skipped on OK entries to keep them small.

    The ``client`` argument identifies the upstream MCP client. If left
    ``None``, the resolver tries env var → psutil → "unknown".
    """
    target = path or _DEFAULT_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts": time.time(),
            "plugin": plugin,
            "tool": tool,
            "args_hash": _hash_args(args),
            "duration_ms": round(duration_ms, 2),
            "status": status,
            "client": client if client is not None else _resolve_client_id(),
        }
        # Enrich only on error to keep ok-path lines small. The same audit
        # log feeds `audit-to-runtime-issues.py` which only cares about
        # error rows.
        if status != "ok":
            if error_message:
                entry["error_message"] = str(error_message)[:500]
            try:
                entry["args_sanitized"] = _sanitize_args(args)
            except Exception:
                # Sanitization itself must never bubble; degrade gracefully.
                entry["args_sanitized"] = "<sanitization_failed>"
        with _lock:
            _rotate_if_needed(target)
            with target.open("a", encoding="utf-8") as fh:
                # ``default=str`` is a defensive fallback: if a custom
                # object slipped past ``_sanitize_args`` (impossible in
                # current code, but cheap insurance), it gets stringified
                # instead of crashing the audit write.
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        # Audit must never break the caller.
        return
