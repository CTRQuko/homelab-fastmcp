"""Tests for the engram HTTP memory backend.

The adapter talks to a running ``engram serve`` over HTTP. Tests mock
``urllib.request.urlopen`` so the real engram instance is never touched.
"""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from core.memory import load_backend
from core.memory.engram import EngramMemory


# ---------------------------------------------------------------------------
# Helpers — fake urllib response
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: bytes | str = b"", status: int = 200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


@contextmanager
def _patch_urlopen(responses: list):
    """Patch urllib.request.urlopen to return queued responses in order.

    Each item in ``responses`` is either a _FakeResp or an Exception (raised).
    """
    iterator = iter(responses)

    def _side_effect(req, timeout=None):
        item = next(iterator)
        if isinstance(item, Exception):
            raise item
        return item

    with patch("urllib.request.urlopen", side_effect=_side_effect) as m:
        yield m


# Init con project: 2 llamadas (GET /health + POST /sessions).
_INIT_WITH_PROJECT = [
    _FakeResp(b"OK", 200),
    _FakeResp(json.dumps({"id": "mimir-router", "status": "created"})),
]
# Init sin project: 1 llamada (solo GET /health).
_INIT_NO_PROJECT = [
    _FakeResp(b"OK", 200),
]
# Default usa project (ruta más común).
_INIT = _INIT_WITH_PROJECT


# ---------------------------------------------------------------------------
# Init / health
# ---------------------------------------------------------------------------

def test_engram_init_health_check_ok():
    """Si /health responde 200 y /sessions OK, la instancia se crea sin error."""
    with _patch_urlopen(_INIT):
        backend = EngramMemory(project="test")
    assert backend.name == "engram"


def test_engram_init_without_project_skips_session_create():
    """Sin project, NO se llama POST /sessions (engram lo requiere)."""
    with _patch_urlopen(_INIT_NO_PROJECT):
        backend = EngramMemory()  # sin project
    assert backend.name == "engram"


def test_engram_init_health_check_unreachable_raises():
    """Si /health no responde, raise RuntimeError loud."""
    fake_err = urllib.error.URLError("Connection refused")
    with _patch_urlopen([fake_err]):
        with pytest.raises(RuntimeError, match="no alcanzable"):
            EngramMemory()


def test_engram_init_health_check_http_error_raises():
    """Si /health devuelve HTTPError, raise."""
    fake_err = urllib.error.HTTPError(
        url="http://x/health", code=500, msg="Server Error",
        hdrs=None, fp=io.BytesIO(b"down"),
    )
    with _patch_urlopen([fake_err]):
        with pytest.raises(RuntimeError):
            EngramMemory()


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def test_engram_save_returns_id_string():
    """save() devuelve el id como str (engram lo da como int)."""
    with _patch_urlopen([
        *_INIT,
        _FakeResp(json.dumps({"id": 42, "status": "saved"})),
    ]) as urlopen_mock:
        backend = EngramMemory(project="testproj")
        rid = backend.save("test content", title="My title", type="discovery")

    assert rid == "42"

    # Verifica el body POST
    last_call = urlopen_mock.call_args_list[-1]
    req = last_call[0][0]
    assert req.method == "POST"
    assert req.full_url.endswith("/observations")
    body = json.loads(req.data.decode())
    assert body["title"] == "My title"
    assert body["content"] == "test content"
    assert body["type"] == "discovery"
    assert body["project"] == "testproj"
    assert body["session_id"] == "mimir-router"


def test_engram_save_default_title_from_content():
    """Si no se pasa title, se usa los primeros 60 chars del content."""
    long_content = "a" * 100
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps({"id": 1, "status": "saved"})),
    ]) as urlopen_mock:
        backend = EngramMemory()
        backend.save(long_content)

    body = json.loads(urlopen_mock.call_args_list[-1][0][0].data.decode())
    assert body["title"] == "a" * 60


def test_engram_save_unexpected_response_raises():
    """Si engram devuelve algo sin 'id' → RuntimeError."""
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps({"foo": "bar"})),
    ]):
        backend = EngramMemory()
        with pytest.raises(RuntimeError, match="respuesta inesperada"):
            backend.save("x")


def test_engram_save_topic_key_passes_through():
    """topic_key kwarg se incluye en el body."""
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps({"id": 7, "status": "saved"})),
    ]) as urlopen_mock:
        backend = EngramMemory()
        backend.save("c", topic_key="release/foo-v1")

    body = json.loads(urlopen_mock.call_args_list[-1][0][0].data.decode())
    assert body["topic_key"] == "release/foo-v1"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def test_engram_search_returns_list():
    sample = [
        {"id": 1, "title": "first", "content": "..."},
        {"id": 2, "title": "second", "content": "..."},
    ]
    with _patch_urlopen([
        *_INIT,
        _FakeResp(json.dumps(sample)),
    ]) as urlopen_mock:
        backend = EngramMemory(project="myproj")
        results = backend.search("query", limit=5)

    assert results == sample

    # URL incluye q, limit, project
    url = urlopen_mock.call_args_list[-1][0][0].full_url
    assert "/search?" in url
    assert "q=query" in url
    assert "limit=5" in url
    assert "project=myproj" in url


def test_engram_search_unexpected_response_raises():
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps({"not": "a list"})),
    ]):
        backend = EngramMemory()
        with pytest.raises(RuntimeError, match="respuesta inesperada"):
            backend.search("q")


# ---------------------------------------------------------------------------
# get / update / delete
# ---------------------------------------------------------------------------

def test_engram_get_returns_dict():
    obs = {"id": 273, "title": "x", "content": "y"}
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps(obs)),
    ]) as urlopen_mock:
        backend = EngramMemory()
        result = backend.get("273")

    assert result == obs
    url = urlopen_mock.call_args_list[-1][0][0].full_url
    assert url.endswith("/observations/273")


def test_engram_update_sends_patch():
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps({"id": 5, "status": "updated"})),
    ]) as urlopen_mock:
        backend = EngramMemory()
        backend.update("5", "new content")

    req = urlopen_mock.call_args_list[-1][0][0]
    assert req.method == "PATCH"
    assert req.full_url.endswith("/observations/5")
    body = json.loads(req.data.decode())
    assert body == {"content": "new content"}


def test_engram_delete_sends_delete():
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        _FakeResp(json.dumps({"id": 99, "status": "deleted"})),
    ]) as urlopen_mock:
        backend = EngramMemory()
        backend.delete("99")

    req = urlopen_mock.call_args_list[-1][0][0]
    assert req.method == "DELETE"
    assert req.full_url.endswith("/observations/99")


# ---------------------------------------------------------------------------
# Errors propagated
# ---------------------------------------------------------------------------

def test_engram_http_error_during_op_raises_runtime_error():
    """HTTPError durante save → RuntimeError con info."""
    fake_err = urllib.error.HTTPError(
        url="http://x", code=400, msg="Bad Request",
        hdrs=None, fp=io.BytesIO(b'{"error":"missing field"}'),
    )
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        fake_err,
    ]):
        backend = EngramMemory()
        with pytest.raises(RuntimeError, match="HTTP 400"):
            backend.save("x")


def test_engram_url_error_during_op_raises_runtime_error():
    """URLError durante search → RuntimeError."""
    fake_err = urllib.error.URLError("Network unreachable")
    with _patch_urlopen([
        *_INIT_NO_PROJECT,
        fake_err,
    ]):
        backend = EngramMemory()
        with pytest.raises(RuntimeError, match="unreachable"):
            backend.search("q")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_load_backend_engram_via_factory():
    with _patch_urlopen(_INIT):
        backend = load_backend("engram", {"project": "test"})
    assert isinstance(backend, EngramMemory)
    assert backend.name == "engram"


def test_load_backend_unknown_lists_engram_in_error():
    """Mensaje de error de backends desconocidos menciona engram."""
    with pytest.raises(ValueError, match="engram"):
        load_backend("does-not-exist")
