"""Tests for core.audit fire-and-forget logger."""
from __future__ import annotations

import json

from core.audit import log_tool_call


def test_log_writes_json_line(tmp_path):
    path = tmp_path / "audit.log"
    log_tool_call("p", "tool_x", {"a": 1}, 12.3, "ok", path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["plugin"] == "p"
    assert entry["tool"] == "tool_x"
    assert entry["status"] == "ok"
    assert entry["duration_ms"] == 12.3
    assert len(entry["args_hash"]) == 16


def test_log_never_raises_on_bad_args(tmp_path):
    path = tmp_path / "audit.log"

    class Weird:
        def __repr__(self):
            return "weird"

    log_tool_call("p", "t", {"x": Weird()}, 0.0, "ok", path=path)
    assert path.exists()


def test_log_appends(tmp_path):
    path = tmp_path / "audit.log"
    log_tool_call("p", "a", {}, 1, "ok", path=path)
    log_tool_call("p", "b", {}, 2, "ok", path=path)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
