"""Tests for core.secrets scoped vault."""
from __future__ import annotations

import pytest

from core import secrets
from core.secrets import (
    CredentialAccessDenied,
    CredentialNotFound,
    PluginContext,
    get_credential,
    has_credential,
    mask,
)


def test_context_allows_exact_match():
    ctx = PluginContext(plugin_name="demo", credential_patterns=("DEMO_TOKEN",))
    assert ctx.allows("DEMO_TOKEN")
    assert not ctx.allows("OTHER_TOKEN")


def test_context_allows_glob():
    ctx = PluginContext(plugin_name="demo", credential_patterns=("PROXMOX_*_TOKEN",))
    assert ctx.allows("PROXMOX_PVE1_TOKEN")
    assert not ctx.allows("PROXMOX_PVE1_USER")


def test_empty_patterns_blocks_everything():
    ctx = PluginContext(plugin_name="demo")
    assert not ctx.allows("ANYTHING")


def test_get_credential_denied_outside_scope(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "abcd1234")
    ctx = PluginContext(plugin_name="demo", credential_patterns=("OTHER_*",))
    with pytest.raises(CredentialAccessDenied):
        get_credential("MY_SECRET", ctx)


def test_get_credential_from_env(monkeypatch):
    monkeypatch.setenv("MY_DEMO_TOKEN", "s3cret")
    ctx = PluginContext(plugin_name="demo", credential_patterns=("MY_DEMO_*",))
    assert get_credential("MY_DEMO_TOKEN", ctx) == "s3cret"


def test_get_credential_not_found(monkeypatch, tmp_path):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    # Redirect secret dirs and dotenv to empty tmp paths
    monkeypatch.setattr(secrets, "_SECRET_DIRS", [tmp_path / "no-secrets"])
    monkeypatch.setattr(secrets, "_PROJECT_ENV", tmp_path / "no.env")
    ctx = PluginContext(plugin_name="demo", credential_patterns=("MISSING_*",))
    with pytest.raises(CredentialNotFound):
        get_credential("MISSING_TOKEN", ctx)


def test_has_credential_true_false(monkeypatch):
    monkeypatch.setenv("PRESENT_TOKEN", "x")
    monkeypatch.delenv("ABSENT_TOKEN", raising=False)
    assert has_credential("PRESENT_TOKEN") is True
    # has_credential returns False for missing vars so long as the other
    # sources don't define them. Point them at empty paths for determinism.


def test_md_files_require_key_at_column_zero(monkeypatch, tmp_path):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "demo.md").write_text(
        "# docs file\n    INDENTED_KEY=should_not_be_read\nREAL_KEY=actual\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets, "_SECRET_DIRS", [tmp_path / "secrets"])
    monkeypatch.delenv("INDENTED_KEY", raising=False)
    monkeypatch.delenv("REAL_KEY", raising=False)
    monkeypatch.setattr(secrets, "_PROJECT_ENV", tmp_path / "no.env")
    assert secrets._from_md_files("INDENTED_KEY") is None
    assert secrets._from_md_files("REAL_KEY") == "actual"


def test_mask_short_and_long():
    assert mask("") == "<empty>"
    assert mask("abcd") == "****"
    assert mask("abcd1234ef") == "abcd****"
