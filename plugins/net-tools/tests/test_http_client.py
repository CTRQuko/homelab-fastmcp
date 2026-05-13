"""Tests for http_client.py — masking + error mapping + retries."""
from __future__ import annotations

import pytest

from net_tools.errors import AuthError, NotFoundError, UpstreamError, ValidationError
from net_tools.http_client import HttpClient, _mask_headers, mask_token

# ---------------------------------------------------------------------------
# mask_token
# ---------------------------------------------------------------------------

def test_mask_token_short():
    assert mask_token("abc") == "***"


def test_mask_token_long():
    masked = mask_token("M51p9ihOwwks-KD6tyBqEW38vj3j1cCi")
    assert masked.startswith("M51p9")
    assert masked.endswith("cCi")
    assert "***" in masked
    assert "ihOwwks" not in masked  # middle must be hidden


def test_mask_token_empty():
    assert mask_token("") == "***"


# ---------------------------------------------------------------------------
# _mask_headers
# ---------------------------------------------------------------------------

def test_mask_headers_redacts_sensitive():
    headers = {
        "Authorization": "Bearer s3cret",
        "X-API-Key": "xyz",
        "User-Agent": "net-tools/0.1.0",
    }
    out = _mask_headers(headers)
    assert out["Authorization"] == "***REDACTED***"
    assert out["X-API-Key"] == "***REDACTED***"
    assert out["User-Agent"] == "net-tools/0.1.0"  # not sensitive


def test_mask_headers_case_insensitive():
    headers = {"authorization": "Bearer x", "COOKIE": "sid=1"}
    out = _mask_headers(headers)
    assert out["authorization"] == "***REDACTED***"
    assert out["COOKIE"] == "***REDACTED***"


# ---------------------------------------------------------------------------
# HttpClient.request — error mapping
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return HttpClient(
        base_url="https://example.test",
        headers={"Authorization": "Bearer test"},
        timeout=5.0,
    )


def test_request_401_raises_auth_error(httpx_mock, client):
    httpx_mock.add_response(url="https://example.test/foo", status_code=401)
    with pytest.raises(AuthError):
        client.request("GET", "/foo")


def test_request_403_raises_auth_error(httpx_mock, client):
    httpx_mock.add_response(url="https://example.test/foo", status_code=403)
    with pytest.raises(AuthError):
        client.request("GET", "/foo")


def test_request_404_raises_not_found(httpx_mock, client):
    httpx_mock.add_response(url="https://example.test/foo", status_code=404, text="nope")
    with pytest.raises(NotFoundError):
        client.request("GET", "/foo")


def test_request_400_raises_validation(httpx_mock, client):
    httpx_mock.add_response(url="https://example.test/foo", status_code=400, text="bad")
    with pytest.raises(ValidationError):
        client.request("GET", "/foo")


def test_request_500_after_retries_raises_upstream(httpx_mock, client, monkeypatch):
    # Disable sleep so retries don't wait.
    monkeypatch.setattr("net_tools.http_client._sleep", lambda s: None)
    # Will be called 3 times: initial + 2 retries.
    for _ in range(3):
        httpx_mock.add_response(url="https://example.test/foo", status_code=503)
    with pytest.raises(UpstreamError):
        client.request("GET", "/foo")


def test_request_non_json_raises_upstream(httpx_mock, client):
    httpx_mock.add_response(
        url="https://example.test/foo",
        status_code=200,
        text="not json",
    )
    with pytest.raises(UpstreamError):
        client.request("GET", "/foo")


def test_request_200_returns_parsed_json(httpx_mock, client):
    httpx_mock.add_response(
        url="https://example.test/foo",
        status_code=200,
        json={"hello": "world"},
    )
    result = client.request("GET", "/foo")
    assert result == {"hello": "world"}
