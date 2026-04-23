"""Tests for core.loader manifest parsing and reconciliation."""
from __future__ import annotations

import textwrap

import pytest

from core.inventory import Inventory
from core.loader import (
    ManifestError,
    discover_manifests,
    evaluate_plugin,
    parse_manifest,
    reconcile,
)


def _mk_plugin(root, name, body):
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return plugin_dir / "plugin.toml"


def test_parse_manifest_minimal(tmp_path):
    path = _mk_plugin(
        tmp_path,
        "p1",
        """
        [plugin]
        name = "p1"
        version = "0.1.0"

        [security]
        inventory_access = []
        credential_refs = []
        """,
    )
    m = parse_manifest(path)
    assert m.name == "p1"
    assert m.version == "0.1.0"
    assert m.enabled is True


def test_parse_manifest_requires_plugin_section(tmp_path):
    path = _mk_plugin(tmp_path, "p", "[security]\n")
    with pytest.raises(ManifestError, match=r"\[plugin\]"):
        parse_manifest(path)


def test_parse_manifest_strict_requires_security(tmp_path):
    path = _mk_plugin(
        tmp_path,
        "p",
        """
        [plugin]
        name = "p"
        version = "0.1.0"
        """,
    )
    with pytest.raises(ManifestError, match="security"):
        parse_manifest(path, strict=True)


def test_parse_manifest_non_strict_allows_missing_security(tmp_path):
    path = _mk_plugin(
        tmp_path,
        "p",
        """
        [plugin]
        name = "p"
        version = "0.1.0"
        """,
    )
    m = parse_manifest(path, strict=False)
    assert m.security == {}


def test_parse_requires_hosts_and_credentials(tmp_path):
    path = _mk_plugin(
        tmp_path,
        "p",
        """
        [plugin]
        name = "p"
        version = "0.1.0"

        [security]

        [[requires.hosts]]
        type = "linux"
        min = 2
        prompt = "need linux hosts"

        [[requires.credentials]]
        pattern = "P_*"
        prompt = "need token"
        """,
    )
    m = parse_manifest(path)
    kinds = sorted(r.kind for r in m.requires)
    assert kinds == ["credentials", "hosts"]


def test_evaluate_plugin_ok_when_inventory_matches(tmp_path):
    (tmp_path / "inv").mkdir()
    (tmp_path / "inv" / "hosts.yaml").write_text(
        "hosts:\n  - name: a\n    type: linux\n    address: 192.0.2.1\n",
        encoding="utf-8",
    )
    inv = Inventory.load(tmp_path / "inv")
    path = _mk_plugin(
        tmp_path,
        "p",
        """
        [plugin]
        name = "p"
        version = "0.1.0"

        [security]

        [[requires.hosts]]
        type = "linux"
        min = 1
        prompt = ""
        """,
    )
    state = evaluate_plugin(parse_manifest(path), inv)
    assert state.status == "ok"
    assert state.missing == []


def test_evaluate_plugin_pending_when_hosts_absent(tmp_path):
    inv = Inventory.load(tmp_path)
    path = _mk_plugin(
        tmp_path,
        "p",
        """
        [plugin]
        name = "p"
        version = "0.1.0"

        [security]

        [[requires.hosts]]
        type = "proxmox"
        min = 1
        prompt = "need proxmox"
        """,
    )
    state = evaluate_plugin(parse_manifest(path), inv)
    assert state.status == "pending_setup"
    assert len(state.missing) == 1


def test_evaluate_disabled_plugin(tmp_path):
    inv = Inventory.load(tmp_path)
    path = _mk_plugin(
        tmp_path,
        "p",
        """
        [plugin]
        name = "p"
        version = "0.1.0"
        enabled = false

        [security]
        """,
    )
    state = evaluate_plugin(parse_manifest(path), inv)
    assert state.status == "disabled"


def test_discover_skips_underscore_dirs(tmp_path):
    _mk_plugin(
        tmp_path / "plugins",
        "_example",
        """
        [plugin]
        name = "example"
        version = "0.0.1"

        [security]
        """,
    )
    _mk_plugin(
        tmp_path / "plugins",
        "real",
        """
        [plugin]
        name = "real"
        version = "1.0.0"

        [security]
        """,
    )
    manifests = discover_manifests(tmp_path / "plugins")
    names = [m.name for m in manifests]
    assert names == ["real"]


def test_credential_requirement_glob_matches_env(tmp_path, monkeypatch):
    from core.loader import _check_requirement, Requirement

    monkeypatch.setenv("PROXMOX_PVE1_TOKEN", "abc")
    req = Requirement(kind="credentials", detail={"pattern": "PROXMOX_*_TOKEN"}, prompt="")
    inv = Inventory.load(tmp_path)
    assert _check_requirement(req, inv) is True


def test_credential_requirement_glob_unmatched(tmp_path, monkeypatch):
    from core.loader import _check_requirement, Requirement

    monkeypatch.delenv("PROXMOX_PVE1_TOKEN", raising=False)
    req = Requirement(kind="credentials", detail={"pattern": "ZZZZZ_*_TOKEN"}, prompt="")
    inv = Inventory.load(tmp_path)
    assert _check_requirement(req, inv) is False


def test_credential_requirement_literal(tmp_path, monkeypatch):
    from core.loader import _check_requirement, Requirement

    monkeypatch.setenv("LITERAL_TOKEN", "v")
    req = Requirement(kind="credentials", detail={"pattern": "LITERAL_TOKEN"}, prompt="")
    inv = Inventory.load(tmp_path)
    assert _check_requirement(req, inv) is True


def test_reconcile_detects_added_and_removed(tmp_path):
    plugins = tmp_path / "plugins"
    _mk_plugin(
        plugins,
        "first",
        """
        [plugin]
        name = "first"
        version = "0.1.0"

        [security]
        """,
    )
    inv = Inventory.load(tmp_path)
    state_path = tmp_path / ".state.json"
    first = reconcile(plugins, inv, state_path)
    assert first.added == ["first"]
    assert first.removed == []

    _mk_plugin(
        plugins,
        "second",
        """
        [plugin]
        name = "second"
        version = "0.1.0"

        [security]
        """,
    )
    second = reconcile(plugins, inv, state_path)
    assert second.added == ["second"]
    assert second.unchanged == ["first"]
