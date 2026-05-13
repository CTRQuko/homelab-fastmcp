"""Tests for adguard/tools.py — mocked httpx, offline.

Covers happy path, validations, idempotency policies and the
``set_rewrites`` atomic diff logic.
"""
from __future__ import annotations

import re

import pytest

from net_tools.adguard import tools as ag_tools


@pytest.fixture(autouse=True)
def ag_env(monkeypatch):
    """Inject one instance ``L1`` for all tests."""
    monkeypatch.setenv("ADGUARD_L1_HOST", "http://10.0.1.14:3000")
    monkeypatch.setenv("ADGUARD_L1_USER", "admin")
    monkeypatch.setenv("ADGUARD_L1_PASSWORD", "test-pass")


# ---------------------------------------------------------------------------
# list_rewrites
# ---------------------------------------------------------------------------

def test_list_rewrites_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[
            {"domain": "www.example.com", "answer": "10.0.1.40"},
            {"domain": "api.example.com", "answer": "10.0.1.41"},
        ],
    )
    result = ag_tools.adguard_list_rewrites("l1")
    assert result["ok"] is True
    assert result["data"]["count"] == 2
    assert result["data"]["host_ref"] == "l1"


def test_list_rewrites_with_domain_filter(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[
            {"domain": "www.example.com", "answer": "10.0.1.40"},
            {"domain": "api.example.com", "answer": "10.0.1.41"},
        ],
    )
    result = ag_tools.adguard_list_rewrites("l1", domain_filter="api")
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["rewrites"][0]["domain"] == "api.example.com"


def test_list_rewrites_unknown_instance():
    result = ag_tools.adguard_list_rewrites("ghost")
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "ADGUARD_GHOST_HOST" in result["error"]


def test_list_rewrites_auth_failure(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        status_code=401,
    )
    result = ag_tools.adguard_list_rewrites("l1")
    assert result["ok"] is False
    assert result["error_type"] == "auth"


# ---------------------------------------------------------------------------
# set_rewrites (atomic bulk replace)
# ---------------------------------------------------------------------------

def test_set_rewrites_rejects_without_confirm():
    result = ag_tools.adguard_set_rewrites("l1", rewrites=[{"domain": "x.com", "answer": "1.1.1.1"}])
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_set_rewrites_rejects_empty_without_explicit_opt_in():
    result = ag_tools.adguard_set_rewrites("l1", rewrites=[], confirm=True)
    assert result["ok"] is False
    assert "wipe ALL" in result["error"]


def test_set_rewrites_rejects_duplicate_entry():
    result = ag_tools.adguard_set_rewrites(
        "l1",
        rewrites=[
            {"domain": "x.com", "answer": "1.1.1.1"},
            {"domain": "x.com", "answer": "1.1.1.1"},
        ],
        confirm=True,
    )
    assert result["ok"] is False
    assert "duplicate" in result["error"].lower()


def test_set_rewrites_rejects_invalid_entry_shape():
    result = ag_tools.adguard_set_rewrites(
        "l1", rewrites=["not-a-dict"], confirm=True,  # type: ignore[list-item]
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_set_rewrites_dry_run_no_calls_to_modify(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[{"domain": "old.example.com", "answer": "1.1.1.1"}],
    )
    result = ag_tools.adguard_set_rewrites(
        "l1",
        rewrites=[{"domain": "new.example.com", "answer": "2.2.2.2"}],
        confirm=True,
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["data"]["applied"] is False
    assert result["data"]["diff"]["added"] == [
        {"domain": "new.example.com", "answer": "2.2.2.2"},
    ]
    assert result["data"]["diff"]["removed"] == [
        {"domain": "old.example.com", "answer": "1.1.1.1"},
    ]


def test_set_rewrites_atomic_apply(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[
            {"domain": "keep.com", "answer": "1.1.1.1"},
            {"domain": "remove.com", "answer": "2.2.2.2"},
        ],
    )
    # Expect: delete remove.com → add new.com
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/delete",
        json={},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/add",
        json={},
    )
    result = ag_tools.adguard_set_rewrites(
        "l1",
        rewrites=[
            {"domain": "keep.com", "answer": "1.1.1.1"},
            {"domain": "new.com", "answer": "3.3.3.3"},
        ],
        confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["applied"] is True
    assert result["data"]["diff"]["unchanged"] == 1
    assert len(result["data"]["diff"]["added"]) == 1
    assert len(result["data"]["diff"]["removed"]) == 1


def test_set_rewrites_allow_empty_wipes_all(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[
            {"domain": "a.com", "answer": "1.1.1.1"},
            {"domain": "b.com", "answer": "2.2.2.2"},
        ],
    )
    httpx_mock.add_response(method="POST", url=re.compile(r".*/rewrite/delete"), json={}, is_reusable=True)
    result = ag_tools.adguard_set_rewrites(
        "l1", rewrites=[], confirm=True, allow_empty=True,
    )
    assert result["ok"] is True
    assert len(result["data"]["diff"]["removed"]) == 2
    assert result["data"]["diff"]["added"] == []


# ---------------------------------------------------------------------------
# add_rewrite
# ---------------------------------------------------------------------------

def test_add_rewrite_rejects_without_confirm():
    result = ag_tools.adguard_add_rewrite("l1", "x.com", "1.1.1.1")
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_add_rewrite_happy_new_domain(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/add",
        json={},
    )
    result = ag_tools.adguard_add_rewrite(
        "l1", "new.com", "1.1.1.1", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "added"


def test_add_rewrite_already_correct_idempotent(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[{"domain": "x.com", "answer": "1.1.1.1"}],
    )
    result = ag_tools.adguard_add_rewrite(
        "l1", "x.com", "1.1.1.1", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "already_correct"


def test_add_rewrite_conflict_without_upsert(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[{"domain": "x.com", "answer": "1.1.1.1"}],
    )
    result = ag_tools.adguard_add_rewrite(
        "l1", "x.com", "2.2.2.2", confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "idempotency"
    assert "1.1.1.1" in str(result.get("context", {}))


def test_add_rewrite_upsert_replaces(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[{"domain": "x.com", "answer": "1.1.1.1"}],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/delete",
        json={},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/add",
        json={},
    )
    result = ag_tools.adguard_add_rewrite(
        "l1", "x.com", "2.2.2.2", upsert=True, confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "updated"


def test_add_rewrite_invalid_answer():
    result = ag_tools.adguard_add_rewrite(
        "l1", "x.com", "not-an-ip-or-host", confirm=True,
    )
    # "not-an-ip-or-host" has no dot → rejected
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# remove_rewrite
# ---------------------------------------------------------------------------

def test_remove_rewrite_already_absent_idempotent(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[],
    )
    result = ag_tools.adguard_remove_rewrite(
        "l1", "ghost.com", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "already_absent"


def test_remove_rewrite_all_for_domain(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[
            {"domain": "x.com", "answer": "1.1.1.1"},
            {"domain": "x.com", "answer": "2.2.2.2"},
            {"domain": "y.com", "answer": "3.3.3.3"},
        ],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/delete",
        json={},
        is_reusable=True,
    )
    result = ag_tools.adguard_remove_rewrite(
        "l1", "x.com", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "deleted"
    assert len(result["data"]["removed"]) == 2


def test_remove_rewrite_specific_answer(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/rewrite/list",
        json=[
            {"domain": "x.com", "answer": "1.1.1.1"},
            {"domain": "x.com", "answer": "2.2.2.2"},
        ],
    )
    httpx_mock.add_response(
        method="POST",
        url="http://10.0.1.14:3000/control/rewrite/delete",
        json={},
    )
    result = ag_tools.adguard_remove_rewrite(
        "l1", "x.com", answer="2.2.2.2", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "deleted"
    assert len(result["data"]["removed"]) == 1
    assert result["data"]["removed"][0]["answer"] == "2.2.2.2"


# ---------------------------------------------------------------------------
# list_filtering_rules
# ---------------------------------------------------------------------------

def test_list_filtering_rules_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/filtering/status",
        json={
            "user_rules": ["||ads.example.com^", "@@||good.example.com^"],
            "filters": [
                {"id": 1, "name": "AdGuard DNS filter", "url": "https://...",
                 "enabled": True, "rules_count": 84512,
                 "last_updated": "2026-05-12T00:00:00Z"},
                {"id": 2, "name": "Disabled list", "url": "https://...",
                 "enabled": False, "rules_count": 100,
                 "last_updated": None},
            ],
        },
    )
    result = ag_tools.adguard_list_filtering_rules("l1")
    assert result["ok"] is True
    assert result["data"]["user_rules_count"] == 2
    assert result["data"]["filter_lists_count"] == 2


def test_list_filtering_rules_enabled_only(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/filtering/status",
        json={
            "user_rules": [],
            "filters": [
                {"id": 1, "name": "Active", "enabled": True, "rules_count": 100},
                {"id": 2, "name": "Off", "enabled": False, "rules_count": 50},
            ],
        },
    )
    result = ag_tools.adguard_list_filtering_rules("l1", enabled_only=True)
    assert result["data"]["filter_lists_count"] == 1
    assert result["data"]["filter_lists"][0]["name"] == "Active"


def test_list_filtering_rules_pattern_filter(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="http://10.0.1.14:3000/control/filtering/status",
        json={
            "user_rules": ["||ads.example.com^", "||tracker.example.com^", "@@||good.example.com^"],
            "filters": [],
        },
    )
    result = ag_tools.adguard_list_filtering_rules("l1", pattern_filter="tracker")
    assert result["data"]["user_rules_count"] == 1


# ---------------------------------------------------------------------------
# query_log_search
# ---------------------------------------------------------------------------

def test_query_log_search_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"http://10\.0\.1\.14:3000/control/querylog.*"),
        json={
            "data": [
                {
                    "time": "2026-05-13T11:00:00Z",
                    "client": "10.0.1.110",
                    "question": {"name": "ads.example.com", "type": "A"},
                    "reason": "FilteredBlackList",
                    "answer": [{"value": "0.0.0.0"}],
                },
            ],
            "oldest": "2026-05-13T10:00:00Z",
        },
    )
    result = ag_tools.adguard_query_log_search("l1", domain_filter="ads")
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    q = result["data"]["queries"][0]
    assert q["domain"] == "ads.example.com"
    assert q["status"] == "FilteredBlackList"


def test_query_log_search_rejects_invalid_response_status():
    result = ag_tools.adguard_query_log_search(
        "l1", response_status="something_wrong",
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_query_log_search_rejects_zero_limit():
    result = ag_tools.adguard_query_log_search("l1", limit=0)
    assert result["ok"] is False


def test_query_log_search_clamps_limit_at_1000(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"http://10\.0\.1\.14:3000/control/querylog.*"),
        json={"data": [], "oldest": None},
    )
    # limit=5000 should not crash; clamped to 1000 silently
    result = ag_tools.adguard_query_log_search("l1", limit=5000)
    assert result["ok"] is True
