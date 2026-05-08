"""Tests for core.bootstrap self-onboarding tools."""
from __future__ import annotations

import textwrap

from core.bootstrap import (
    router_add_credential,
    router_add_host,
    router_add_service,
    router_help,
    router_status,
)
from core.inventory import Inventory
from core.loader import LoadReport, parse_manifest


def _mk_manifest(dirpath, name, credential_refs):
    dirpath.mkdir(parents=True, exist_ok=True)
    refs = ", ".join(f'"{r}"' for r in credential_refs)
    (dirpath / "plugin.toml").write_text(
        textwrap.dedent(
            f"""
            [plugin]
            name = "{name}"
            version = "0.1.0"

            [security]
            credential_refs = [{refs}]
            """
        ),
        encoding="utf-8",
    )
    return parse_manifest(dirpath / "plugin.toml")


def test_router_help_lists_bootstrap_tools():
    info = router_help()
    assert "router_status" in info["available_bootstrap_tools"]
    assert "router_add_host" in info["available_bootstrap_tools"]


def test_router_status_reports_inventory(tmp_path):
    inv = Inventory.load(tmp_path)
    report = LoadReport(plugins=[])
    status = router_status(inv, report, memory_backend="noop")
    assert status["memory_backend"] == "noop"
    assert status["inventory"]["hosts_total"] == 0


def test_add_host_then_add_service(tmp_path):
    res = router_add_host(
        tmp_path,
        name="srv1",
        type="linux",
        address="192.0.2.50",
        port=22,
        credential_ref="SRV1_KEY",
        auth_method="ssh_key",
        tags=["dev"],
    )
    assert res["ok"]

    svc = router_add_service(
        tmp_path,
        name="web",
        type="generic",
        host_ref="srv1",
        port=8443,
    )
    assert svc["ok"]

    inv = Inventory.load(tmp_path)
    assert [h.name for h in inv.get_hosts()] == ["srv1"]
    assert [s.name for s in inv.get_services()] == ["web"]


def test_add_host_rejects_invalid_type_before_persisting(tmp_path):
    """Regresion: 2026-05-06 un host con type='ubiquiti-switch' fue persistido
    a hosts.yaml y reventó el siguiente boot del router. La validacion debe
    correr ANTES del append, devolviendo error legible sin tocar disco.
    """
    res = router_add_host(
        tmp_path,
        name="ubi-switch",
        type="ubiquiti-switch",  # NO está en _VALID_HOST_TYPES
        address="10.0.1.99",
    )
    assert res["ok"] is False
    assert "ubiquiti-switch" in res["error"]
    assert "network-device" in res["error"]  # sugiere alternativa válida
    # hosts.yaml NO debe haberse creado (refusa antes de tocar disco)
    assert not (tmp_path / "hosts.yaml").exists()


def test_add_host_accepts_all_valid_types(tmp_path):
    """Cada type del whitelist debe pasar sin error."""
    from core.inventory import _VALID_HOST_TYPES
    for i, htype in enumerate(_VALID_HOST_TYPES):
        res = router_add_host(
            tmp_path,
            name=f"host-{i}",
            type=htype,
            address=f"192.0.2.{i + 1}",
        )
        assert res["ok"], f"type='{htype}' should be valid: {res}"


def test_add_credential_rejected_when_no_plugin_pattern(tmp_path):
    report = LoadReport(plugins=[])
    res = router_add_credential("FOO_TOKEN", "x", report, vault_dir=tmp_path)
    assert res["ok"] is False
    assert "No loaded plugin" in res["error"]


def test_add_credential_accepted_with_matching_pattern(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo"
    manifest = _mk_manifest(plugin_dir, "demo", ["DEMO_*"])
    from core.loader import PluginState

    report = LoadReport(plugins=[PluginState(manifest=manifest, status="ok")])
    res = router_add_credential(
        "DEMO_TOKEN",
        "s3cretVALUE",
        report,
        vault_dir=tmp_path / "vault",
    )
    assert res["ok"] is True
    assert res["preview"].endswith("****")
    vault_file = tmp_path / "vault" / "router_vault.md"
    assert vault_file.exists()
    content = vault_file.read_text(encoding="utf-8")
    assert "DEMO_TOKEN=s3cretVALUE" in content


def test_add_credential_rejects_bad_ref_format(tmp_path):
    report = LoadReport(plugins=[])
    res = router_add_credential("lower_case", "x", report, vault_dir=tmp_path)
    assert res["ok"] is False


def test_add_credential_rejects_newline_in_value(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo"
    manifest = _mk_manifest(plugin_dir, "demo", ["DEMO_*"])
    from core.loader import PluginState

    report = LoadReport(plugins=[PluginState(manifest=manifest, status="ok")])
    res = router_add_credential(
        "DEMO_A",
        "legit\nATTACKER_TOKEN=evil",
        report,
        vault_dir=tmp_path / "vault",
    )
    assert res["ok"] is False
    assert "newline" in res["error"]
    # Nothing should have been written
    assert not (tmp_path / "vault" / "router_vault.md").exists()


def test_add_credential_rejects_cr_and_nul(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo"
    manifest = _mk_manifest(plugin_dir, "demo", ["DEMO_*"])
    from core.loader import PluginState

    report = LoadReport(plugins=[PluginState(manifest=manifest, status="ok")])
    for bad in ("a\rb", "a\x00b"):
        res = router_add_credential("DEMO_A", bad, report, vault_dir=tmp_path / "vault")
        assert res["ok"] is False


def test_disabled_plugin_does_not_widen_allowlist(tmp_path):
    """A malicious plugin.toml with enabled=false must not grant credential scope."""
    import textwrap

    from core.loader import PluginState, parse_manifest

    plugin_dir = tmp_path / "plugins" / "evil"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent(
            """
            [plugin]
            name = "evil"
            version = "0.1.0"
            enabled = false

            [security]
            credential_refs = ["*"]
            """
        ),
        encoding="utf-8",
    )
    manifest = parse_manifest(plugin_dir / "plugin.toml")
    report = LoadReport(plugins=[PluginState(manifest=manifest, status="disabled")])
    res = router_add_credential("ANY_TOKEN", "x", report, vault_dir=tmp_path / "vault")
    assert res["ok"] is False


def test_pending_setup_plugin_still_allows_adding_its_credential(tmp_path):
    """Bootstrap flow must work: pending_setup plugin should permit its own ref."""
    plugin_dir = tmp_path / "plugins" / "demo"
    manifest = _mk_manifest(plugin_dir, "demo", ["DEMO_*"])
    from core.loader import PluginState

    report = LoadReport(plugins=[PluginState(manifest=manifest, status="pending_setup")])
    res = router_add_credential(
        "DEMO_TOKEN", "v", report, vault_dir=tmp_path / "vault"
    )
    assert res["ok"] is True


def test_add_credential_overwrites_existing_ref(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo"
    manifest = _mk_manifest(plugin_dir, "demo", ["DEMO_*"])
    from core.loader import PluginState

    report = LoadReport(plugins=[PluginState(manifest=manifest, status="ok")])
    router_add_credential("DEMO_A", "old", report, vault_dir=tmp_path / "vault")
    router_add_credential("DEMO_A", "new", report, vault_dir=tmp_path / "vault")
    content = (tmp_path / "vault" / "router_vault.md").read_text(encoding="utf-8")
    assert "DEMO_A=new" in content
    assert "DEMO_A=old" not in content
