"""Tests for scripts/audit_to_runtime_issues.py.

The script reads ``audit.log`` (JSONL) produced by ``core.audit`` and
appends skeleton entries to ``runtime-issues.md``. Tests use synthetic
audit log fixtures and tmp_path to avoid touching real artifacts.
"""
from __future__ import annotations

# The script lives under scripts/, not core/. Import dynamically.
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _load_script_module():
    repo = Path(__file__).resolve().parent.parent
    path = repo / "scripts" / "audit_to_runtime_issues.py"
    spec = importlib.util.spec_from_file_location("audit_to_runtime", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def script():
    return _load_script_module()


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------

def test_parse_since_relative_hours(script):
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    result = script.parse_since("2 hours ago", now=now)
    assert result == now - timedelta(hours=2)


def test_parse_since_relative_minutes(script):
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    result = script.parse_since("30 minutes ago", now=now)
    assert result == now - timedelta(minutes=30)


def test_parse_since_relative_days(script):
    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    result = script.parse_since("3 days ago", now=now)
    assert result == now - timedelta(days=3)


def test_parse_since_iso_datetime(script):
    result = script.parse_since("2026-05-06T10:00:00")
    assert result.year == 2026 and result.month == 5 and result.day == 6
    assert result.hour == 10


def test_parse_since_iso_space_separator(script):
    result = script.parse_since("2026-05-06 10:00")
    assert result.year == 2026 and result.hour == 10


def test_parse_since_unix_timestamp(script):
    result = script.parse_since("1715000000")
    assert result.timestamp() == 1715000000.0


def test_parse_since_invalid_raises(script):
    with pytest.raises(ValueError, match="No pude parsear"):
        script.parse_since("yesterday afternoon")


# ---------------------------------------------------------------------------
# iter_error_entries
# ---------------------------------------------------------------------------

def _write_audit_log(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def test_iter_error_entries_filters_status_ok(tmp_path, script):
    log = tmp_path / "audit.log"
    _write_audit_log(log, [
        {"ts": 100, "plugin": "p", "tool": "t", "status": "ok"},
        {"ts": 101, "plugin": "p", "tool": "t", "status": "error:X"},
    ])
    result = list(script.iter_error_entries(log, since_ts=0))
    assert len(result) == 1
    assert result[0]["status"] == "error:X"


def test_iter_error_entries_filters_by_timestamp(tmp_path, script):
    log = tmp_path / "audit.log"
    _write_audit_log(log, [
        {"ts": 100, "plugin": "p", "tool": "t", "status": "error:A"},  # too old
        {"ts": 200, "plugin": "p", "tool": "t", "status": "error:B"},  # in window
    ])
    result = list(script.iter_error_entries(log, since_ts=150))
    assert len(result) == 1
    assert result[0]["status"] == "error:B"


def test_iter_error_entries_skips_malformed_lines(tmp_path, script):
    log = tmp_path / "audit.log"
    log.write_text(
        '{"ts": 100, "plugin": "p", "tool": "t", "status": "error:X"}\n'
        'not-json-line\n'
        '\n'
        '{"ts": 101, "plugin": "p", "tool": "t", "status": "error:Y"}\n',
        encoding="utf-8",
    )
    result = list(script.iter_error_entries(log, since_ts=0))
    assert len(result) == 2


def test_iter_error_entries_missing_log_returns_empty(tmp_path, script):
    log = tmp_path / "nonexistent.log"
    result = list(script.iter_error_entries(log, since_ts=0))
    assert result == []


# ---------------------------------------------------------------------------
# group_errors
# ---------------------------------------------------------------------------

def test_group_errors_clusters_by_plugin_tool_message(script):
    entries = [
        {"ts": 100, "plugin": "homelab", "tool": "ssh_run",
         "status": "error:Timeout", "error_message": "Timeout 30s",
         "args_hash": "h1", "client": "claude-code"},
        {"ts": 101, "plugin": "homelab", "tool": "ssh_run",
         "status": "error:Timeout", "error_message": "Timeout 30s",
         "args_hash": "h2", "client": "opencode"},
        {"ts": 102, "plugin": "homelab", "tool": "list_lxc",
         "status": "error:HTTPError", "error_message": "401 Unauthorized",
         "args_hash": "h3", "client": "claude-code"},
    ]
    groups = script.group_errors(entries)
    assert len(groups) == 2
    timeout_group = next(g for g in groups if g["tool"] == "ssh_run")
    assert timeout_group["count"] == 2
    assert set(timeout_group["clients"]) == {"claude-code", "opencode"}
    assert timeout_group["first_ts"] == 100
    assert "h1" in timeout_group["args_hashes"]
    assert "h2" in timeout_group["args_hashes"]


def test_group_errors_truncates_message_for_grouping_key(script):
    """Mensajes con mismo prefijo pero distintos paths/IPs deben agrupar."""
    entries = [
        {"ts": 100, "plugin": "p", "tool": "t", "status": "error:X",
         "error_message": "Timeout en " + "x" * 250 + " /node/A",
         "args_hash": "h"},
        {"ts": 101, "plugin": "p", "tool": "t", "status": "error:X",
         "error_message": "Timeout en " + "x" * 250 + " /node/B",
         "args_hash": "h"},
    ]
    groups = script.group_errors(entries)
    # Truncate at 200 chars → ambos caen en el mismo grupo.
    assert len(groups) == 1
    assert groups[0]["count"] == 2


def test_group_errors_keeps_only_3_most_recent_samples(script):
    entries = [
        {"ts": float(i), "plugin": "p", "tool": "t", "status": "error:X",
         "error_message": "msg", "args_hash": f"h{i}"}
        for i in range(10)
    ]
    groups = script.group_errors(entries)
    assert groups[0]["count"] == 10
    assert len(groups[0]["samples"]) == 3
    # The 3 most recent samples kept (ts=7, 8, 9).
    assert [s["ts"] for s in groups[0]["samples"]] == [7.0, 8.0, 9.0]


# ---------------------------------------------------------------------------
# render_entry / render_section
# ---------------------------------------------------------------------------

def test_render_entry_includes_required_fields(script):
    group = {
        "plugin": "homelab", "tool": "ssh_run",
        "error_message": "Timeout tras 30s ejecutando comando en 'pve'",
        "first_ts": 1715000000.0,
        "count": 4,
        "args_hashes": ["h1", "h2"],
        "clients": {"claude-code", "opencode"},
        "samples": [],
    }
    out = script.render_entry(group, session_tag="claude-test-1")
    # Required pieces in skeleton
    assert "homelab.ssh_run" in out
    assert "Timeout tras 30s" in out
    assert "claude-test-1" in out
    assert "<pendiente" in out  # placeholders for operator
    assert "count=4" in out
    assert "h1,h2" in out


def test_render_section_empty_returns_empty_string(script):
    assert script.render_section([], session_tag="x") == ""


def test_render_section_includes_auto_generated_marker(script):
    group = {
        "plugin": "p", "tool": "t",
        "error_message": "bad", "first_ts": 1715000000.0,
        "count": 1, "args_hashes": ["h"], "clients": {"c"}, "samples": [],
    }
    out = script.render_section([group], session_tag="ses-1")
    assert "auto-generated" in out
    assert "ses-1" in out


# ---------------------------------------------------------------------------
# append_to_md (with backup)
# ---------------------------------------------------------------------------

def test_append_to_md_creates_backup_before_writing(tmp_path, script):
    md = tmp_path / "runtime-issues.md"
    md.write_text("# Existing content\n\nLine\n", encoding="utf-8")
    script.append_to_md(md, "\n## NEW SECTION\n")
    # Backup exists with original content.
    bak = tmp_path / "runtime-issues.md.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "# Existing content\n\nLine\n"
    # Active file has both old + new.
    new = md.read_text(encoding="utf-8")
    assert "# Existing content" in new
    assert "NEW SECTION" in new


def test_append_to_md_creates_file_if_missing(tmp_path, script):
    md = tmp_path / "subdir" / "runtime-issues.md"
    script.append_to_md(md, "## NEW\n")
    assert md.exists()
    assert "NEW" in md.read_text(encoding="utf-8")
    # No backup (file didn't exist).
    assert not (tmp_path / "subdir" / "runtime-issues.md.bak").exists()


def test_append_to_md_empty_content_is_noop(tmp_path, script):
    md = tmp_path / "runtime-issues.md"
    md.write_text("original\n", encoding="utf-8")
    script.append_to_md(md, "")
    # File unchanged, no backup created (empty content short-circuit).
    assert md.read_text(encoding="utf-8") == "original\n"
    assert not (tmp_path / "runtime-issues.md.bak").exists()


# ---------------------------------------------------------------------------
# main / CLI integration
# ---------------------------------------------------------------------------

def test_main_dry_run_prints_skeleton(tmp_path, script, capsys):
    log = tmp_path / "audit.log"
    md = tmp_path / "runtime-issues.md"
    _write_audit_log(log, [
        {"ts": datetime.now(timezone.utc).timestamp() - 60,
         "plugin": "homelab", "tool": "ssh_run", "status": "error:Timeout",
         "error_message": "Timeout 30s", "args_hash": "h1",
         "client": "claude-code"},
    ])
    rc = script.main([
        "--audit-log", str(log),
        "--append-to", str(md),
        "--since", "1 hour ago",
        "--session-tag", "test-1",
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr()
    assert "homelab.ssh_run" in out.out
    assert "DRY RUN" in out.err
    # md NOT touched.
    assert not md.exists()


def test_main_appends_to_md(tmp_path, script):
    log = tmp_path / "audit.log"
    md = tmp_path / "runtime-issues.md"
    md.write_text("# preexistente\n", encoding="utf-8")
    _write_audit_log(log, [
        {"ts": datetime.now(timezone.utc).timestamp() - 60,
         "plugin": "homelab", "tool": "ssh_run", "status": "error:Timeout",
         "error_message": "Timeout 30s", "args_hash": "h1",
         "client": "claude-code"},
    ])
    rc = script.main([
        "--audit-log", str(log),
        "--append-to", str(md),
        "--since", "1 hour ago",
        "--session-tag", "test-2",
    ])
    assert rc == 0
    content = md.read_text(encoding="utf-8")
    assert "# preexistente" in content
    assert "homelab.ssh_run" in content
    assert (tmp_path / "runtime-issues.md.bak").exists()


def test_main_no_errors_returns_0_and_no_changes(tmp_path, script):
    log = tmp_path / "audit.log"
    md = tmp_path / "runtime-issues.md"
    md.write_text("original\n", encoding="utf-8")
    _write_audit_log(log, [
        {"ts": datetime.now(timezone.utc).timestamp(),
         "plugin": "p", "tool": "t", "status": "ok", "args_hash": "h"},
    ])
    rc = script.main([
        "--audit-log", str(log),
        "--append-to", str(md),
        "--since", "1 hour ago",
    ])
    assert rc == 0
    # md unchanged, no backup.
    assert md.read_text(encoding="utf-8") == "original\n"
    assert not (tmp_path / "runtime-issues.md.bak").exists()
