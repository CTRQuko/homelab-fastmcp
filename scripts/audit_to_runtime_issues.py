#!/usr/bin/env python3
"""audit-to-runtime-issues.py — Build runtime-issues.md skeleton entries from audit.log.

Reads ``mimir-mcp`` ``audit.log`` (JSONL produced by ``core.audit``), filters
entries with ``status != "ok"`` since a cutoff timestamp, groups them by
``(plugin, tool, error_message)`` and appends a skeleton entry per group to
``docs/operator-notes/runtime-issues.md`` for the operator to complete with
``causa`` / ``fix aplicado`` / ``prevención``.

This is the bridge between the structured audit log (machine) and the
narrative incident log (human). Designed for cross-LLM/CLI usage: any
client that invokes mimir-mcp passes through ``audit.log``, so this tool
captures errors from Claude Code, OpenCode, future clients, etc.

Usage::

    # From a Claude Code Stop hook (auto):
    python scripts/audit_to_runtime_issues.py \\
        --since-session-start \\
        --append-to docs/operator-notes/runtime-issues.md \\
        --session-tag "claude-${CLAUDE_SESSION_ID}"

    # Manual dry-run:
    python scripts/audit_to_runtime_issues.py --since "2 hours ago" --dry-run

The script never modifies the audit log itself. The runtime-issues.md is
backed up to ``runtime-issues.md.bak`` (single rotating slot) before any
write. Append-only: never replaces existing content.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# Default audit log path resolution mirrors core.audit (same env vars,
# same fallback to <framework_root>/config/audit.log).
_FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent


def _default_audit_log() -> Path:
    explicit = os.environ.get("MIMIR_AUDIT_LOG")
    if explicit:
        return Path(explicit)
    legacy = os.environ.get("HOMELAB_FASTMCP_AUDIT_LOG")
    if legacy:
        return Path(legacy)
    return _FRAMEWORK_ROOT / "config" / "audit.log"


def _default_runtime_issues_md() -> Path:
    return _FRAMEWORK_ROOT / "docs" / "operator-notes" / "runtime-issues.md"


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

_RELATIVE_RE = re.compile(
    r"^\s*(\d+)\s*(second|minute|hour|day|week)s?\s*ago\s*$",
    re.IGNORECASE,
)
_UNIT_TO_DELTA: dict[str, timedelta] = {
    "second": timedelta(seconds=1),
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse ``--since`` argument.

    Accepted forms:
      - ``"2 hours ago"``, ``"30 minutes ago"``, ``"1 day ago"`` (relative)
      - ``"2026-05-06T10:00:00"``, ``"2026-05-06 10:00:00"`` (ISO-ish)
      - Unix timestamp as string (``"1715000000"``)

    Returns timezone-aware UTC datetime.
    """
    now = now or datetime.now(timezone.utc)
    s = value.strip()

    rel = _RELATIVE_RE.match(s)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2).lower()
        return now - amount * _UNIT_TO_DELTA[unit]

    # Unix timestamp
    if s.replace(".", "", 1).isdigit():
        return datetime.fromtimestamp(float(s), tz=timezone.utc)

    # ISO-ish — try with and without 'T' separator.
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise ValueError(
        f"No pude parsear --since={value!r}. Usa "
        f"'<N> {'/'.join(_UNIT_TO_DELTA.keys())} ago', ISO datetime, o "
        f"unix timestamp."
    )


# ---------------------------------------------------------------------------
# Audit log parsing
# ---------------------------------------------------------------------------

def iter_error_entries(
    log_path: Path, *, since_ts: float
) -> Iterable[dict[str, Any]]:
    """Yield audit entries with ``status != "ok"`` and ``ts >= since_ts``.

    Robust against malformed lines (skip silently). Returns a generator —
    caller must materialize if needed for grouping.
    """
    if not log_path.exists():
        return
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("ts")
                if not isinstance(ts, (int, float)) or ts < since_ts:
                    continue
                status = entry.get("status", "")
                if not isinstance(status, str) or status == "ok":
                    continue
                yield entry
    except OSError:
        return


def group_errors(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group entries by (plugin, tool, error_message). Returns list of
    aggregated dicts: one per cluster, with first-occurrence ``ts``,
    list of ``args_hash`` seen, and count.
    """
    groups: dict[tuple, dict[str, Any]] = defaultdict(  # type: ignore[arg-type]
        lambda: {"count": 0, "args_hashes": [], "first_ts": None,
                 "clients": set(), "samples": []}
    )
    for entry in entries:
        plugin = entry.get("plugin", "?")
        tool = entry.get("tool", "?")
        err_msg = entry.get("error_message") or entry.get("status", "<no message>")
        # Truncar la clave del grupo a 200 chars para evitar grupos
        # divergentes por mensajes con paths/IPs distintas.
        err_msg_key = err_msg[:200]
        key = (plugin, tool, err_msg_key)
        g = groups[key]
        g["count"] += 1
        g["plugin"] = plugin
        g["tool"] = tool
        g["error_message"] = err_msg
        ts = entry.get("ts")
        if g["first_ts"] is None or (isinstance(ts, (int, float)) and ts < g["first_ts"]):
            g["first_ts"] = ts
        g["args_hashes"].append(entry.get("args_hash", "?"))
        client = entry.get("client", "unknown")
        g["clients"].add(client)
        # Keep up to 3 sample entries for context (most-recent kept).
        g["samples"].append(entry)
        if len(g["samples"]) > 3:
            g["samples"].pop(0)
    return list(groups.values())


# ---------------------------------------------------------------------------
# Markdown skeleton generation
# ---------------------------------------------------------------------------

_SKELETON_TEMPLATE = """\
### [{date}] {tool} falló — {error_short} (sesión: {session_tag})
- agente: {clients}
- **Síntoma**: {error_message}
- **Causa**: <pendiente — completar al revisar>
- **Fix aplicado**: <pendiente>
- **Prevención**: <pendiente>
- **Audit raw**: count={count} first_ts={first_ts_iso} args_hashes={args_hashes}
"""


def _short_error(msg: str, *, max_len: int = 70) -> str:
    """First line of the error trimmed for the title."""
    line = msg.strip().splitlines()[0] if msg.strip() else "error"
    if len(line) > max_len:
        return line[:max_len].rstrip() + "..."
    return line


def render_entry(group: dict[str, Any], *, session_tag: str) -> str:
    first_ts = group.get("first_ts")
    if isinstance(first_ts, (int, float)):
        dt = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        date = dt.strftime("%Y-%m-%d %H%M")
        first_ts_iso = dt.isoformat()
    else:
        date = "????-??-?? ????"
        first_ts_iso = "?"
    plugin = group.get("plugin", "?")
    tool_short = group.get("tool", "?")
    tool_qualified = f"{plugin}.{tool_short}" if plugin != "?" else tool_short
    return _SKELETON_TEMPLATE.format(
        date=date,
        tool=tool_qualified,
        error_short=_short_error(group.get("error_message", "")),
        session_tag=session_tag,
        clients=", ".join(sorted(group.get("clients", []) or ["unknown"])),
        error_message=group.get("error_message", "").strip().replace("\n", " "),
        count=group.get("count", 1),
        first_ts_iso=first_ts_iso,
        args_hashes=",".join(group.get("args_hashes", [])[:5])
        + ("..." if len(group.get("args_hashes", [])) > 5 else ""),
    )


def render_section(
    groups: list[dict[str, Any]], *, session_tag: str
) -> str:
    """Build the full block to append: header comment + one entry per group."""
    if not groups:
        return ""
    header = (
        f"\n<!-- auto-generated by audit-to-runtime-issues.py "
        f"session={session_tag} on {datetime.now(timezone.utc).isoformat()} -->\n\n"
    )
    parts = [header]
    for g in groups:
        parts.append(render_entry(g, session_tag=session_tag))
        parts.append("\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Append + backup
# ---------------------------------------------------------------------------

def append_to_md(md_path: Path, content: str) -> None:
    """Append ``content`` to ``md_path``. Creates a single ``.bak`` first.

    If ``md_path`` does not exist, creates it (no backup needed).
    """
    if not content:
        return
    if md_path.exists():
        backup = md_path.with_suffix(md_path.suffix + ".bak")
        backup.write_bytes(md_path.read_bytes())
    else:
        md_path.parent.mkdir(parents=True, exist_ok=True)
    with md_path.open("a", encoding="utf-8") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audit-to-runtime-issues",
        description=(
            "Extract errors from mimir audit.log and append skeleton "
            "entries to runtime-issues.md."
        ),
    )
    p.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="Override audit log path (default: MIMIR_AUDIT_LOG or "
        "<framework_root>/config/audit.log)",
    )
    p.add_argument(
        "--append-to",
        type=Path,
        default=None,
        help="runtime-issues.md path (default: "
        "<framework_root>/docs/operator-notes/runtime-issues.md)",
    )
    since_group = p.add_mutually_exclusive_group()
    since_group.add_argument(
        "--since",
        type=str,
        default="2 hours ago",
        help='Cutoff timestamp. Forms: "<N> hours/minutes/... ago", ISO, '
        'unix ts. Default: "2 hours ago".',
    )
    since_group.add_argument(
        "--since-session-start",
        action="store_true",
        help="Use process parent's create_time as cutoff (psutil required). "
        "Falls back to '2 hours ago' if psutil unavailable.",
    )
    p.add_argument(
        "--session-tag",
        type=str,
        default=None,
        help="Tag included in the skeleton entries (default: auto from time).",
    )
    p.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help=(
            "Path to a state file storing the last-processed audit ts. "
            "When set: overrides --since with the stored ts (initial run "
            "falls back to --since), and after a successful append updates "
            "it to the max ts seen. Use this for cron jobs to avoid "
            "duplicate skeleton entries between runs."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the would-be appended content; do not modify files.",
    )
    return p


# ---------------------------------------------------------------------------
# State file (for cron / repeated invocations)
# ---------------------------------------------------------------------------

def read_state_ts(state_file: Path | None) -> float | None:
    """Read last-processed ts from state file. Returns None if absent or
    malformed — caller should fall back to --since.
    """
    if state_file is None or not state_file.exists():
        return None
    try:
        text = state_file.read_text(encoding="utf-8").strip()
        return float(text)
    except (OSError, ValueError):
        return None


def write_state_ts(state_file: Path | None, ts: float) -> None:
    """Persist last-processed ts. Creates parent dir if needed.

    Never raises — failure is silent (the next run just re-processes a
    few entries, which the .md already shows are duplicates the operator
    can clean up).
    """
    if state_file is None:
        return
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(f"{ts}\n", encoding="utf-8")
    except OSError:
        return


def _resolve_session_start_ts() -> float | None:
    try:
        import psutil  # type: ignore[import-not-found]
        parent = psutil.Process().parent()
        if parent is None:
            return None
        return float(parent.create_time())
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    audit_log = args.audit_log or _default_audit_log()
    md_path = args.append_to or _default_runtime_issues_md()

    # Cutoff resolution priority: --state-file (if exists) > --since-session-start > --since.
    # The state file path takes precedence so cron loops don't re-process entries.
    state_ts = read_state_ts(args.state_file)
    if state_ts is not None:
        since_dt = datetime.fromtimestamp(state_ts, tz=timezone.utc)
    elif args.since_session_start:
        ts_start = _resolve_session_start_ts()
        if ts_start is None:
            since_dt = parse_since("2 hours ago")
        else:
            since_dt = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    else:
        since_dt = parse_since(args.since)
    since_ts = since_dt.timestamp()

    session_tag = args.session_tag or f"auto-{datetime.now().strftime('%Y%m%d-%H%M')}"

    entries = list(iter_error_entries(audit_log, since_ts=since_ts))
    groups = group_errors(entries)
    block = render_section(groups, session_tag=session_tag)

    if not block:
        print(
            f"[audit-to-runtime] No errors since {since_dt.isoformat()} "
            f"in {audit_log}. Nothing to append.",
            file=sys.stderr,
        )
        # Even when nothing changed, advance the state cursor to "now" so
        # the next cron tick doesn't re-scan the same window unnecessarily.
        if args.state_file is not None and not args.dry_run:
            write_state_ts(args.state_file, datetime.now(timezone.utc).timestamp())
        return 0

    if args.dry_run:
        sys.stdout.write(block)
        print(
            f"\n[audit-to-runtime] DRY RUN — {len(groups)} group(s), "
            f"{len(entries)} entries since {since_dt.isoformat()}",
            file=sys.stderr,
        )
        return 0

    append_to_md(md_path, block)

    # Update state to the max ts seen so subsequent cron runs skip these.
    if args.state_file is not None:
        max_ts = max(
            (e.get("ts", 0.0) for e in entries if isinstance(e.get("ts"), (int, float))),
            default=since_ts,
        )
        write_state_ts(args.state_file, float(max_ts))

    print(
        f"[audit-to-runtime] Appended {len(groups)} group(s) "
        f"({len(entries)} entries) to {md_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
