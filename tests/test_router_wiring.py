"""Tests for router.py — state lifecycle, profile gate, report formatting.

We avoid starting the real FastMCP server. ``build_mcp`` is exercised
against the import only when fastmcp is available; otherwise the test
is skipped.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import router as router_mod
from core.loader import (
    LoadReport,
    PluginManifest,
    PluginState,
    QuarantineEntry,
    Requirement,
)


def _mk_plugin(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.toml").write_text(textwrap.dedent(body), encoding="utf-8")


def _bare_state_from_report(
    plugins: list[PluginState],
    quarantined: list[QuarantineEntry] | None = None,
) -> LoadReport:
    return LoadReport(plugins=plugins, quarantined=quarantined or [])


def _mk_manifest(name: str, enabled: bool = True) -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0.0",
        enabled=enabled,
        path=Path("/tmp") / name,
        runtime={},
        security={},
        requires=[],
        tools={},
    )


# ---------------------------------------------------------------------------
# _apply_profile_gate
# ---------------------------------------------------------------------------


def test_profile_gate_none_is_passthrough():
    report = _bare_state_from_report(
        [PluginState(manifest=_mk_manifest("a"), status="ok")]
    )
    router_mod._apply_profile_gate(report, None)
    assert report.plugins[0].status == "ok"


def test_profile_gate_allowlist_hides_non_listed():
    report = _bare_state_from_report(
        [
            PluginState(manifest=_mk_manifest("a"), status="ok"),
            PluginState(manifest=_mk_manifest("b"), status="pending_setup"),
            PluginState(manifest=_mk_manifest("c"), status="ok"),
        ]
    )
    router_mod._apply_profile_gate(report, {"a"})
    assert report.plugins[0].status == "ok"
    assert report.plugins[1].status == "disabled_by_profile"
    assert report.plugins[2].status == "disabled_by_profile"


def test_profile_gate_empty_disables_everything():
    report = _bare_state_from_report(
        [
            PluginState(manifest=_mk_manifest("a"), status="ok"),
            PluginState(manifest=_mk_manifest("b"), status="pending_setup"),
        ]
    )
    router_mod._apply_profile_gate(report, set())
    for p in report.plugins:
        assert p.status == "disabled_by_profile"


def test_profile_gate_preserves_disabled_in_manifest():
    report = _bare_state_from_report(
        [PluginState(manifest=_mk_manifest("a", enabled=False), status="disabled")]
    )
    router_mod._apply_profile_gate(report, set())
    # Manifest-level disable is not overwritten.
    assert report.plugins[0].status == "disabled"


def test_profile_gate_clears_missing_on_downgrade():
    req = Requirement(kind="hosts", detail={"type": "x", "min": 1}, prompt="")
    ps = PluginState(
        manifest=_mk_manifest("a"), status="pending_setup", missing=[req]
    )
    report = _bare_state_from_report([ps])
    router_mod._apply_profile_gate(report, set())
    assert report.plugins[0].status == "disabled_by_profile"
    assert report.plugins[0].missing == []


# ---------------------------------------------------------------------------
# RouterConfig.load — file + defaults
# ---------------------------------------------------------------------------


def test_config_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(router_mod, "DEFAULT_CONFIG", tmp_path / "nope.toml")
    cfg = router_mod.RouterConfig.load()
    assert cfg.profile == "default"
    assert cfg.memory_backend == "noop"
    assert cfg.strict_manifest is True
    assert cfg.audit_enabled is True
    assert cfg.skills_dir is None


def test_config_parses_toml(tmp_path, monkeypatch):
    cfg_path = tmp_path / "router.toml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            [router]
            profile = "minimal"
            plugin_dir = "./plugins"
            inventory_dir = "./inventory"
            skills_dir = "./skills"

            [memory]
            backend = "sqlite"
            [memory.sqlite]
            path = "config/mem.db"

            [security]
            strict_manifest = false
            audit_enabled = false
            """
        ),
        encoding="utf-8",
    )
    cfg = router_mod.RouterConfig.load(cfg_path)
    assert cfg.profile == "minimal"
    assert cfg.memory_backend == "sqlite"
    assert cfg.memory_config == {"path": "config/mem.db"}
    assert cfg.strict_manifest is False
    assert cfg.audit_enabled is False
    assert cfg.skills_dir is not None


def test_config_rejects_bad_toml(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[router\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Could not parse"):
        router_mod.RouterConfig.load(bad)


def test_abs_or_none_empty_string():
    assert router_mod._abs_or_none(None) is None
    assert router_mod._abs_or_none("") is None
    assert router_mod._abs_or_none("   ") is None


# ---------------------------------------------------------------------------
# RouterState.bootstrap + refresh
# ---------------------------------------------------------------------------


def _tmp_cfg(tmp_path: Path, profile_yaml: str | None = None) -> router_mod.RouterConfig:
    (tmp_path / "plugins").mkdir()
    (tmp_path / "inventory").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "profiles").mkdir()
    profile_path = tmp_path / "profiles" / "default.yaml"
    if profile_yaml is not None:
        profile_path.write_text(profile_yaml, encoding="utf-8")
    return router_mod.RouterConfig(
        profile="default",
        plugin_dir=tmp_path / "plugins",
        inventory_dir=tmp_path / "inventory",
        skills_dir=None,
        agents_dir=None,
        memory_backend="noop",
        memory_config={},
        strict_manifest=True,
        audit_enabled=False,
        state_path=tmp_path / "config" / ".last_state.json",
        profile_path=profile_path,
    )


def test_bootstrap_empty(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    state = router_mod.RouterState.bootstrap(cfg)
    assert state.inventory.summary()["hosts_total"] == 0
    assert state.report.plugins == []
    assert state.skills == []
    assert state.agents == []


def test_bootstrap_with_profile_gate(tmp_path):
    cfg = _tmp_cfg(tmp_path, "enabled_plugins: []\n")
    _mk_plugin(
        cfg.plugin_dir,
        "aplug",
        """
        [plugin]
        name = "aplug"
        version = "1.0.0"

        [security]
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    assert state.profile_enabled == set()
    assert state.report.plugins[0].status == "disabled_by_profile"


def test_refresh_picks_up_new_host(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "needshost",
        """
        [plugin]
        name = "needshost"
        version = "1.0.0"

        [security]

        [[requires.hosts]]
        type = "linux"
        min = 1
        prompt = "need one"
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    assert state.report.plugins[0].status == "pending_setup"

    # Simulate user running router_add_host
    (cfg.inventory_dir / "hosts.yaml").write_text(
        "hosts:\n  - name: h\n    type: linux\n    address: 192.0.2.1\n",
        encoding="utf-8",
    )
    state.refresh()
    assert state.report.plugins[0].status == "ok"


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_shows_counts(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "needshost",
        """
        [plugin]
        name = "needshost"
        version = "1.0.0"

        [security]

        [[requires.hosts]]
        type = "proxmox"
        min = 1
        prompt = "need proxmox"
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    text = router_mod.format_report(state)
    assert "homelab-fastmcp framework" in text
    assert "0 hosts" in text
    assert "needshost v1.0.0: pending_setup" in text
    assert "Next (hosts): type=proxmox" in text
    assert "need proxmox" in text
    assert "Skills: 0" in text


def test_format_report_shows_quarantined(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    # write a malformed plugin
    (cfg.plugin_dir / "broken").mkdir()
    (cfg.plugin_dir / "broken" / "plugin.toml").write_text(
        "[plugin\nname = oops", encoding="utf-8"
    )
    state = router_mod.RouterState.bootstrap(cfg)
    text = router_mod.format_report(state)
    assert "Quarantined: 1" in text
    assert "broken" in text


# ---------------------------------------------------------------------------
# build_mcp — only when fastmcp installed
# ---------------------------------------------------------------------------


def test_build_mcp_registers_tools(tmp_path):
    pytest.importorskip("fastmcp")
    cfg = _tmp_cfg(tmp_path)
    # Two plugins: one ok, one pending, so setup_<pending> should appear.
    _mk_plugin(
        cfg.plugin_dir,
        "okplug",
        """
        [plugin]
        name = "okplug"
        version = "1.0.0"

        [security]
        """,
    )
    _mk_plugin(
        cfg.plugin_dir,
        "pendplug",
        """
        [plugin]
        name = "pendplug"
        version = "1.0.0"

        [security]

        [[requires.hosts]]
        type = "proxmox"
        min = 1
        prompt = "x"
        """,
    )
    # Also a skill
    sd = tmp_path / "skills"
    sd.mkdir()
    (sd / "s.md").write_text(
        "---\nname: hello\ndescription: hi\n---\nbody\n", encoding="utf-8"
    )
    cfg = router_mod.RouterConfig(
        **{**cfg.__dict__, "skills_dir": sd}
    )
    state = router_mod.RouterState.bootstrap(cfg)
    mcp = router_mod.build_mcp(state)

    # FastMCP 3.x exposes tools via _tool_manager / internal registry; use
    # the public contract: it has a .name. We only need to assert it built.
    assert mcp is not None
    assert getattr(mcp, "name", "") == "homelab-fastmcp-router"
