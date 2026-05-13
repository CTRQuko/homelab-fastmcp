"""Tests for cloudflare/tools.py — mocks all httpx calls.

NO TOCA LA API REAL. httpx.Client is patched with respx (via
pytest-httpx) so every test runs offline.
"""
from __future__ import annotations

import re
from unittest.mock import patch  # noqa: F401  # may be used in extensions

import pytest

from net_tools.cloudflare import tools as cf_tools
from net_tools.errors import ValidationError  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cf_env(monkeypatch):
    """Inject valid CLOUDFLARE_TOKEN + CLOUDFLARE_ZONE_ID for all tests."""
    monkeypatch.setenv("CLOUDFLARE_TOKEN", "test-token-not-real")
    monkeypatch.setenv("CLOUDFLARE_ZONE_ID", "test-zone-id")
    # Disable proxy override by default (each test that needs it sets it)
    monkeypatch.delenv("NETTOOLS_ALLOW_PROXIED", raising=False)


def _cf_envelope(result, success=True, errors=None, total_pages=1):
    """Build the {success, result, errors, result_info} CF API shape."""
    return {
        "success": success,
        "errors": errors or [],
        "result": result,
        "result_info": {"total_pages": total_pages, "page": 1, "count": 1},
    }


def _mock_record(
    rid="rec123",
    name="www.example.com",
    type="A",
    content="192.0.2.1",
    proxied=False,
    ttl=1,
    comment=None,
    priority=None,
):
    r = {
        "id": rid, "name": name, "type": type, "content": content,
        "proxied": proxied, "ttl": ttl,
    }
    if comment:
        r["comment"] = comment
    if priority is not None:
        r["priority"] = priority
    return r


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------

def test_list_records_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records?page=1&per_page=100",
        json=_cf_envelope(
            [_mock_record(), _mock_record(rid="rec456", name="api.example.com", content="192.0.2.2")]
        ),
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id",
        json=_cf_envelope({"name": "example.com"}),
    )
    result = cf_tools.cloudflare_dns_list_records()
    assert result["ok"] is True
    assert result["data"]["zone_name"] == "example.com"
    assert result["data"]["count"] == 2


def test_list_records_filters_by_name_clientside(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([
            _mock_record(name="www.example.com"),
            _mock_record(rid="rec456", name="api.example.com"),
        ]),
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id",
        json=_cf_envelope({"name": "example.com"}),
    )
    result = cf_tools.cloudflare_dns_list_records(name_filter="api")
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["records"][0]["name"] == "api.example.com"


def test_list_records_rejects_invalid_type():
    result = cf_tools.cloudflare_dns_list_records(type_filter="WAT")
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "WAT" in result["error"]


def test_list_records_auth_failure(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        status_code=403,
        text="Forbidden",
    )
    # The auth failure happens BEFORE the zone metadata call, so no
    # need to mock it.
    result = cf_tools.cloudflare_dns_list_records()
    assert result["ok"] is False
    assert result["error_type"] == "auth"


# ---------------------------------------------------------------------------
# get_record
# ---------------------------------------------------------------------------

def test_get_record_found(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([_mock_record()]),
    )
    result = cf_tools.cloudflare_dns_get_record(name="www.example.com")
    assert result["ok"] is True
    assert result["data"]["name"] == "www.example.com"


def test_get_record_not_found(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([]),
    )
    result = cf_tools.cloudflare_dns_get_record(name="nope.example.com")
    assert result["ok"] is False
    assert result["error_type"] == "not_found"


def test_get_record_ambiguous(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([
            _mock_record(rid="r1", name="x.example.com", type="A", content="1.1.1.1"),
            _mock_record(rid="r2", name="x.example.com", type="AAAA", content="::1"),
        ]),
    )
    result = cf_tools.cloudflare_dns_get_record(name="x.example.com")
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "ambiguous" in result["error"].lower()


def test_get_record_empty_name():
    result = cf_tools.cloudflare_dns_get_record(name="")
    assert result["ok"] is False
    assert result["error_type"] == "validation"


# ---------------------------------------------------------------------------
# create_record
# ---------------------------------------------------------------------------

def test_create_record_rejects_without_confirm():
    result = cf_tools.cloudflare_dns_create_record(
        name="new.example.com", type="A", content="1.2.3.4",
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "confirm" in result["error"].lower()


def test_create_record_rejects_proxied_true_without_override():
    result = cf_tools.cloudflare_dns_create_record(
        name="new.example.com", type="A", content="1.2.3.4",
        proxied=True, confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "proxied" in result["error"].lower()


def test_create_record_proxied_override_allows(httpx_mock, monkeypatch):
    monkeypatch.setenv("NETTOOLS_ALLOW_PROXIED", "true")
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([]),
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records",
        json=_cf_envelope(_mock_record(rid="newrec", name="new.example.com", proxied=True)),
    )
    result = cf_tools.cloudflare_dns_create_record(
        name="new.example.com", type="A", content="1.2.3.4",
        proxied=True, confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "created"


def test_create_record_rejects_invalid_content_for_type():
    result = cf_tools.cloudflare_dns_create_record(
        name="bad.example.com", type="A", content="not-an-ip",
        confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "ipv4" in result["error"].lower()


def test_create_record_rejects_invalid_ttl():
    result = cf_tools.cloudflare_dns_create_record(
        name="t.example.com", type="A", content="1.2.3.4",
        ttl=15,  # < 60
        confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_create_record_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([]),  # idempotency check: no existing
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records",
        json=_cf_envelope(_mock_record(rid="newrec", name="new.example.com")),
    )
    result = cf_tools.cloudflare_dns_create_record(
        name="new.example.com", type="A", content="1.2.3.4",
        ttl=1, comment="test", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["id"] == "newrec"
    assert result["data"]["action"] == "created"


def test_create_record_idempotency_collision(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"https://api\.cloudflare\.com/client/v4/zones/test-zone-id/dns_records.*"),
        json=_cf_envelope([_mock_record(rid="existing-id")]),
    )
    result = cf_tools.cloudflare_dns_create_record(
        name="www.example.com", type="A", content="1.2.3.4",
        confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "idempotency"
    assert result["context"]["existing_id"] == "existing-id"


def test_create_record_mx_requires_priority():
    result = cf_tools.cloudflare_dns_create_record(
        name="mail.example.com", type="MX", content="mx1.example.com",
        confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "priority" in result["error"].lower()


# ---------------------------------------------------------------------------
# update_record
# ---------------------------------------------------------------------------

def test_update_record_rejects_without_confirm():
    result = cf_tools.cloudflare_dns_update_record(
        record_id="rec123", content="9.9.9.9",
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_update_record_rejects_no_op():
    result = cf_tools.cloudflare_dns_update_record(
        record_id="rec123", confirm=True,
    )
    assert result["ok"] is False
    assert result["error_type"] == "validation"
    assert "no-op" in result["error"].lower()


def test_update_record_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records/rec123",
        json=_cf_envelope(_mock_record()),
    )
    httpx_mock.add_response(
        method="PATCH",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records/rec123",
        json=_cf_envelope(_mock_record(content="9.9.9.9")),
    )
    result = cf_tools.cloudflare_dns_update_record(
        record_id="rec123", content="9.9.9.9", confirm=True,
    )
    assert result["ok"] is True
    assert result["data"]["action"] == "updated"


# ---------------------------------------------------------------------------
# delete_record
# ---------------------------------------------------------------------------

def test_delete_record_rejects_without_confirm():
    result = cf_tools.cloudflare_dns_delete_record(record_id="rec123")
    assert result["ok"] is False
    assert result["error_type"] == "validation"


def test_delete_record_happy_path(httpx_mock):
    httpx_mock.add_response(
        method="DELETE",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records/rec123",
        json=_cf_envelope({"id": "rec123"}),
    )
    result = cf_tools.cloudflare_dns_delete_record(record_id="rec123", confirm=True)
    assert result["ok"] is True
    assert result["data"]["action"] == "deleted"


def test_delete_record_idempotent_when_already_absent(httpx_mock):
    httpx_mock.add_response(
        method="DELETE",
        url="https://api.cloudflare.com/client/v4/zones/test-zone-id/dns_records/ghost",
        status_code=404,
        text="not found",
    )
    result = cf_tools.cloudflare_dns_delete_record(record_id="ghost", confirm=True)
    assert result["ok"] is True
    assert result["data"]["action"] == "already_absent"
