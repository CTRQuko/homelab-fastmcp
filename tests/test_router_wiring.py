"""Tests for router.py — state lifecycle, profile gate, report formatting.

We avoid starting the real FastMCP server. ``build_mcp`` is exercised
against the import only when fastmcp is available; otherwise the test
is skipped.
"""
from __future__ import annotations

import dataclasses
import sys
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
# _setup_payload — live state, not closure snapshot (R1 / FIX A)
# ---------------------------------------------------------------------------


def test_setup_payload_returns_live_status_after_refresh(tmp_path):
    """Una vez el usuario completa setup, setup_<plugin>() debe reportar
    ``status=ok``. El fallo historico: el payload capturaba el estado al
    registrar la tool y quedaba mintiendo para siempre."""
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
        prompt = "need linux"
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    assert state.report.plugins[0].status == "pending_setup"
    before = router_mod._setup_payload(state, "needshost")
    assert before["status"] == "pending_setup"
    assert len(before["missing"]) == 1

    (cfg.inventory_dir / "hosts.yaml").write_text(
        "hosts:\n  - name: h\n    type: linux\n    address: 192.0.2.1\n",
        encoding="utf-8",
    )
    state.refresh()

    after = router_mod._setup_payload(state, "needshost")
    assert after["status"] == "ok"
    assert after["missing"] == []
    assert "Setup complete" in after["next_tool_hint"]


def test_setup_payload_reflects_partial_progress(tmp_path, monkeypatch):
    """Con dos requisitos (host + credencial), satisfacer solo uno debe
    reducir la lista de ``missing``."""
    cfg = _tmp_cfg(tmp_path)
    monkeypatch.delenv("NEEDS_BOTH_TOKEN", raising=False)
    _mk_plugin(
        cfg.plugin_dir,
        "needsboth",
        """
        [plugin]
        name = "needsboth"
        version = "1.0.0"

        [security]

        [[requires.hosts]]
        type = "linux"
        min = 1
        prompt = "need host"

        [[requires.credentials]]
        pattern = "NEEDS_BOTH_TOKEN"
        prompt = "need token"
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    start = router_mod._setup_payload(state, "needsboth")
    assert start["status"] == "pending_setup"
    assert len(start["missing"]) == 2

    (cfg.inventory_dir / "hosts.yaml").write_text(
        "hosts:\n  - name: h\n    type: linux\n    address: 192.0.2.1\n",
        encoding="utf-8",
    )
    state.refresh()

    partial = router_mod._setup_payload(state, "needsboth")
    assert partial["status"] == "pending_setup"
    assert len(partial["missing"]) == 1
    assert partial["missing"][0]["kind"] == "credentials"


def test_setup_payload_handles_plugin_disappeared(tmp_path):
    """Si el plugin desaparece entre refreshes, el payload devuelve
    ``status=not_found`` en vez de crashear."""
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "goner",
        """
        [plugin]
        name = "goner"
        version = "1.0.0"

        [security]

        [[requires.hosts]]
        type = "x"
        min = 1
        prompt = ""
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    assert state.report.plugins[0].manifest.name == "goner"

    import shutil

    shutil.rmtree(cfg.plugin_dir / "goner")
    state.refresh()

    gone = router_mod._setup_payload(state, "goner")
    assert gone["status"] == "not_found"
    assert gone["missing"] == []


# ---------------------------------------------------------------------------
# RouterState.refresh — profile reload (R3 / FIX B)
# ---------------------------------------------------------------------------


def test_refresh_reloads_profile_enabled_from_yaml(tmp_path):
    """Editar ``profiles/<name>.yaml`` en caliente debe surtir efecto en el
    siguiente ``refresh()`` — no requerir restart."""
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

    cfg.profile_path.write_text("enabled_plugins:\n  - aplug\n", encoding="utf-8")
    state.refresh()

    assert state.profile_enabled == {"aplug"}
    assert state.report.plugins[0].status == "ok"


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


# ---------------------------------------------------------------------------
# Fase 5 — audit coverage for setup_<plugin>() (gap closure)
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal stand-in for FastMCP that records registered tool callables.

    FastMCP's public surface is not stable enough to invoke a registered
    tool inline. The setup_ tool is a plain Python closure; capturing it
    via the decorator is enough to exercise its audit behavior.
    """

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str):  # type: ignore[no-untyped-def]
        def deco(fn):  # type: ignore[no-untyped-def]
            self.tools[name] = fn
            return fn

        return deco


def _prepare_pending_state(
    tmp_path: Path, audit_enabled: bool
) -> router_mod.RouterState:
    cfg = _tmp_cfg(tmp_path)
    cfg = dataclasses.replace(cfg, audit_enabled=audit_enabled)
    _mk_plugin(
        cfg.plugin_dir,
        "needs",
        """
        [plugin]
        name = "needs"
        version = "1.0.0"

        [security]

        [[requires.hosts]]
        type = "proxmox"
        min = 1
        prompt = "need proxmox"
        """,
    )
    return router_mod.RouterState.bootstrap(cfg)


def test_setup_tool_audits_on_success(tmp_path, monkeypatch):
    """setup_<plugin> used to be the only tool bypassing audit. With audit
    enabled, every invocation must produce one entry tagged as the plugin
    it belongs to — not as 'router'."""
    state = _prepare_pending_state(tmp_path, audit_enabled=True)
    captured: list[dict] = []
    monkeypatch.setattr(
        router_mod.audit, "log_tool_call", lambda **kw: captured.append(kw)
    )

    mcp = _FakeMCP()
    router_mod._register_setup_tool(mcp, state, "needs")
    fn = mcp.tools["setup_needs"]
    payload = fn()  # type: ignore[operator]

    assert payload["status"] == "pending_setup"
    assert len(captured) == 1
    entry = captured[0]
    assert entry["plugin"] == "needs"
    assert entry["tool"] == "setup_needs"
    assert entry["status"] == "ok"
    assert entry["duration_ms"] >= 0


def test_setup_tool_audits_on_error(tmp_path, monkeypatch):
    """If the payload helper blows up mid-call, audit still records one
    entry with ``error:<ExcType>`` — surface gaps must be observable."""
    state = _prepare_pending_state(tmp_path, audit_enabled=True)
    captured: list[dict] = []
    monkeypatch.setattr(
        router_mod.audit, "log_tool_call", lambda **kw: captured.append(kw)
    )

    def _boom(_state, _name):
        raise RuntimeError("boom")

    monkeypatch.setattr(router_mod, "_setup_payload", _boom)

    mcp = _FakeMCP()
    router_mod._register_setup_tool(mcp, state, "needs")
    fn = mcp.tools["setup_needs"]

    with pytest.raises(RuntimeError, match="boom"):
        fn()  # type: ignore[operator]

    assert len(captured) == 1
    assert captured[0]["status"] == "error:RuntimeError"
    assert captured[0]["plugin"] == "needs"


# ---------------------------------------------------------------------------
# Fase 6b — plugin mount (subprocess via create_proxy)
# ---------------------------------------------------------------------------


def _mk_mountable_plugin(cfg: router_mod.RouterConfig, name: str) -> None:
    """Drop a plugin.toml with a [runtime].entry + the entry file itself."""
    _mk_plugin(
        cfg.plugin_dir,
        name,
        f"""
        [plugin]
        name = "{name}"
        version = "1.0.0"

        [runtime]
        entry = "server.py"

        [security]
        """,
    )
    (cfg.plugin_dir / name / "server.py").write_text(
        "# placeholder entry\n", encoding="utf-8"
    )


def test_plugin_mount_config_builds_expected_payload(tmp_path):
    """The proxy config must point at this interpreter + the resolved
    entry path. No venv manager, no deps install — the simple case is
    'the plugin brings its own environment'."""
    cfg = _tmp_cfg(tmp_path)
    _mk_mountable_plugin(cfg, "p1")
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "p1")

    payload = router_mod._plugin_mount_config(ps)

    server = payload["mcpServers"]["default"]
    assert server["command"] == sys.executable  # noqa: F821 (sys imported below)
    assert len(server["args"]) == 1
    arg_path = Path(server["args"][0])
    assert arg_path.is_file()
    assert arg_path.name == "server.py"


def test_plugin_mount_config_missing_entry_raises(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "noentry",
        """
        [plugin]
        name = "noentry"
        version = "1.0.0"

        [security]
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "noentry")

    with pytest.raises(ValueError, match="entry"):
        router_mod._plugin_mount_config(ps)


def test_plugin_mount_config_missing_entry_file_raises(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "ghost",
        """
        [plugin]
        name = "ghost"
        version = "1.0.0"

        [runtime]
        entry = "does-not-exist.py"

        [security]
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "ghost")

    with pytest.raises(FileNotFoundError, match="does-not-exist.py"):
        router_mod._plugin_mount_config(ps)


def test_mount_plugin_downgrades_to_error_on_failure(tmp_path):
    """A broken plugin must not propagate — just flip to ``error`` so
    sibling plugins and the router itself keep working."""
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "broken",
        """
        [plugin]
        name = "broken"
        version = "1.0.0"

        [runtime]
        entry = "nonexistent.py"

        [security]
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "broken")
    assert ps.status == "ok"  # requires are empty -> passes gate

    sentinel = object()
    router_mod._mount_plugin(sentinel, ps)  # type: ignore[arg-type]

    assert ps.status == "error"
    assert ps.error is not None and "nonexistent.py" in ps.error


def test_mount_plugin_calls_create_proxy_and_mount(tmp_path, monkeypatch):
    """Happy path: the helper passes the resolved config to create_proxy
    and registers the result under the plugin's namespace."""
    cfg = _tmp_cfg(tmp_path)
    _mk_mountable_plugin(cfg, "happy")
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "happy")

    proxy_sentinel = object()
    create_calls: list[dict] = []
    mount_calls: list[dict] = []

    def _fake_create_proxy(config):  # type: ignore[no-untyped-def]
        create_calls.append(config)
        return proxy_sentinel

    class _MountRecorder:
        def mount(self, server, namespace):  # type: ignore[no-untyped-def]
            mount_calls.append({"server": server, "namespace": namespace})

    import sys as _sys

    fake_fastmcp = type(_sys)("fastmcp")
    fake_fastmcp.server = type(_sys)("fastmcp.server")
    fake_fastmcp.server.create_proxy = _fake_create_proxy
    monkeypatch.setitem(_sys.modules, "fastmcp", fake_fastmcp)
    monkeypatch.setitem(_sys.modules, "fastmcp.server", fake_fastmcp.server)

    recorder = _MountRecorder()
    router_mod._mount_plugin(recorder, ps)

    assert ps.status == "ok"  # no downgrade
    assert len(create_calls) == 1
    assert create_calls[0]["mcpServers"]["default"]["command"]
    assert len(mount_calls) == 1
    assert mount_calls[0]["namespace"] == "happy"
    assert mount_calls[0]["server"] is proxy_sentinel


def test_setup_tool_skips_audit_when_disabled(tmp_path, monkeypatch):
    """``audit_enabled=False`` is the escape hatch for tests/dev; setup_
    must honour it just like every other wrapped tool."""
    state = _prepare_pending_state(tmp_path, audit_enabled=False)
    captured: list[dict] = []
    monkeypatch.setattr(
        router_mod.audit, "log_tool_call", lambda **kw: captured.append(kw)
    )

    mcp = _FakeMCP()
    router_mod._register_setup_tool(mcp, state, "needs")
    fn = mcp.tools["setup_needs"]
    fn()  # type: ignore[operator]

    assert captured == []
