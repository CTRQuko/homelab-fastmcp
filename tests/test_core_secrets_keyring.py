"""Tests for the keyring resolution layer in core.secrets.

The real OS keyring is never touched: tests monkeypatch the lazy
``keyring`` import to a stub module so behaviour can be exercised on
CI runners that have no backend, headless Linux containers, etc.
"""
from __future__ import annotations

import sys
import types

import pytest

from core import secrets


@pytest.fixture
def fake_keyring(monkeypatch):
    """Inject a controllable ``keyring`` stub into ``sys.modules``.

    Returns the stub object so each test can configure
    ``get_password``/``set_password`` behaviour. The stub is removed
    after the test so other suites are unaffected.
    """
    stub = types.SimpleNamespace(
        store={},
        get_password=lambda service, key: stub.store.get((service, key)),
        set_password=lambda service, key, value: stub.store.__setitem__(
            (service, key), value
        ),
    )
    monkeypatch.setitem(sys.modules, "keyring", stub)
    yield stub
    monkeypatch.delitem(sys.modules, "keyring", raising=False)


def test_keyring_returns_value_when_available(fake_keyring, monkeypatch):
    """If keyring has a value, _resolve returns it (env unset, md files
    skipped because the keyring source short-circuits the chain)."""
    monkeypatch.delenv("FAKE_KEY", raising=False)
    fake_keyring.store[("mimir", "FAKE_KEY")] = "from-keyring"
    monkeypatch.setattr(secrets, "_SECRET_DIRS", [])
    monkeypatch.setattr(secrets, "_PROJECT_ENV", secrets.Path("/nonexistent"))

    assert secrets._resolve("FAKE_KEY") == "from-keyring"


def test_keyring_swallows_exceptions(monkeypatch, tmp_path):
    """If keyring raises (broken backend), fall through to the next
    resolution source — vault file in this case."""

    def boom(service, key):
        raise RuntimeError("backend explodió")

    bad = types.SimpleNamespace(get_password=boom, set_password=boom)
    monkeypatch.setitem(sys.modules, "keyring", bad)
    monkeypatch.delenv("FAKE_KEY", raising=False)

    md_dir = tmp_path / "secrets"
    md_dir.mkdir()
    (md_dir / "x.md").write_text("FAKE_KEY=from-vault\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "_SECRET_DIRS", [md_dir])
    monkeypatch.setattr(secrets, "_PROJECT_ENV", tmp_path / "nonexistent.env")

    assert secrets._resolve("FAKE_KEY") == "from-vault"


def test_env_wins_over_keyring(fake_keyring, monkeypatch):
    """Process env var beats keyring even if both are populated."""
    monkeypatch.setenv("FAKE_KEY", "from-env")
    fake_keyring.store[("mimir", "FAKE_KEY")] = "from-keyring"

    assert secrets._resolve("FAKE_KEY") == "from-env"


def test_keyring_wins_over_vault_file(fake_keyring, monkeypatch, tmp_path):
    """With env unset, keyring beats the vault file."""
    monkeypatch.delenv("FAKE_KEY", raising=False)
    fake_keyring.store[("mimir", "FAKE_KEY")] = "from-keyring"

    md_dir = tmp_path / "secrets"
    md_dir.mkdir()
    (md_dir / "x.md").write_text("FAKE_KEY=from-vault\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "_SECRET_DIRS", [md_dir])
    monkeypatch.setattr(secrets, "_PROJECT_ENV", tmp_path / "nonexistent.env")

    assert secrets._resolve("FAKE_KEY") == "from-keyring"


def test_set_keyring_returns_true_when_backend_works(fake_keyring):
    assert secrets.set_keyring("FAKE_KEY", "v") is True
    assert fake_keyring.store[("mimir", "FAKE_KEY")] == "v"


def test_set_keyring_returns_false_on_exception(monkeypatch):
    def boom(service, key, value):
        raise RuntimeError("no backend")

    bad = types.SimpleNamespace(set_password=boom, get_password=lambda *a: None)
    monkeypatch.setitem(sys.modules, "keyring", bad)

    assert secrets.set_keyring("FAKE_KEY", "v") is False
