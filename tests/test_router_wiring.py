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
        allow_plugin_install=False,
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
    assert "[mimir] router" in text
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
    assert getattr(mcp, "name", "") == "mimir"


# ---------------------------------------------------------------------------
# Fase 5 — audit coverage for setup_<plugin>() (gap closure)
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal stand-in for FastMCP that records registered tool callables.

    FastMCP's public surface is not stable enough to invoke a registered
    tool inline. Accepts both decorator shapes the router uses —
    ``@mcp.tool`` bare and ``mcp.tool(name=...)`` factory — and also
    :meth:`mount` / :meth:`add_middleware` so the richer tests that
    drive the full :func:`build_mcp` flow can reuse it.
    """

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.mounts: list[tuple] = []
        self.middlewares: list[object] = []

    def tool(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if args and callable(args[0]):
            fn = args[0]
            self.tools[fn.__name__] = fn
            return fn
        name = kwargs.get("name")

        def deco(fn):  # type: ignore[no-untyped-def]
            self.tools[name or fn.__name__] = fn
            return fn

        return deco

    def mount(self, server, namespace):  # type: ignore[no-untyped-def]
        self.mounts.append((server, namespace))

    def add_middleware(self, mw):  # type: ignore[no-untyped-def]
        self.middlewares.append(mw)


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


# ---------------------------------------------------------------------------
# Fase 6c — [tools].whitelist/blacklist middleware enforcement
# ---------------------------------------------------------------------------


def _mk_policy_plugin(
    cfg: router_mod.RouterConfig,
    name: str,
    *,
    whitelist: list[str] | None = None,
    blacklist: list[str] | None = None,
) -> None:
    """Same as _mk_mountable_plugin but with a [tools] policy attached."""
    wl = ", ".join(f'"{p}"' for p in (whitelist or []))
    bl = ", ".join(f'"{p}"' for p in (blacklist or []))
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

        [tools]
        whitelist = [{wl}]
        blacklist = [{bl}]
        """,
    )
    (cfg.plugin_dir / name / "server.py").write_text(
        "# placeholder entry\n", encoding="utf-8"
    )


def test_build_tool_policy_skips_plugins_without_policy(tmp_path):
    """A plugin with empty whitelist/blacklist adds zero middleware cost —
    no entry in the policy dict means the hot path never sees it."""
    cfg = _tmp_cfg(tmp_path)
    _mk_mountable_plugin(cfg, "nopolicy")
    _mk_policy_plugin(cfg, "withpolicy", blacklist=["dangerous_*"])
    state = router_mod.RouterState.bootstrap(cfg)

    policy = router_mod._build_tool_policy(state)

    assert "nopolicy" not in policy
    assert "withpolicy" in policy
    assert policy["withpolicy"]["blacklist"] == ["dangerous_*"]


def test_build_tool_policy_skips_non_ok_plugins(tmp_path):
    """Plugins in error/pending/disabled don't expose tools, so including
    their policy would be dead weight and potentially misleading."""
    cfg = _tmp_cfg(tmp_path)
    _mk_policy_plugin(cfg, "ok_plug", blacklist=["bad"])
    # A plugin with unmet requires lands in pending_setup.
    _mk_plugin(
        cfg.plugin_dir,
        "pending_plug",
        """
        [plugin]
        name = "pending_plug"
        version = "1.0.0"

        [security]

        [tools]
        blacklist = ["something"]

        [[requires.hosts]]
        type = "proxmox"
        min = 1
        prompt = "need it"
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)

    policy = router_mod._build_tool_policy(state)

    assert "ok_plug" in policy
    assert "pending_plug" not in policy


def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCallToolCtx:
    def __init__(self, name: str) -> None:
        self.message = type("M", (), {"name": name})()


def test_middleware_filters_denied_tools_from_list(tmp_path):
    """The LLM should never even see tools denied by [tools] — if it
    doesn't know they exist, it can't ask for them."""
    policy = {"demo": {"blacklist": ["destroy_*"], "whitelist": []}}
    mw = router_mod._make_tool_filter_middleware(policy)

    async def _call_next(_ctx):
        return [
            _FakeTool("demo_read"),
            _FakeTool("demo_destroy_all"),
            _FakeTool("router_help"),  # outside namespace: always allowed
        ]

    out = _run_async(mw.on_list_tools(None, _call_next))

    names = [t.name for t in out]
    assert "demo_read" in names
    assert "demo_destroy_all" not in names
    assert "router_help" in names


def test_middleware_respects_whitelist_only(tmp_path):
    """With a non-empty whitelist, anything not explicitly allowed is
    dropped (fail-closed semantics matching tool_allowed()). Patterns
    in the manifest are matched against the LOCAL tool name — the
    namespace prefix is stripped before the check so plugin authors
    don't need to know how the router composes full names."""
    policy = {"demo": {"whitelist": ["list_*"], "blacklist": []}}
    mw = router_mod._make_tool_filter_middleware(policy)

    async def _call_next(_ctx):
        return [
            _FakeTool("demo_list_hosts"),
            _FakeTool("demo_write_file"),
        ]

    out = _run_async(mw.on_list_tools(None, _call_next))

    names = [t.name for t in out]
    assert names == ["demo_list_hosts"]


def test_middleware_blocks_call_of_denied_tool(tmp_path):
    """Defence in depth: a client with a stale list_tools cache still
    cannot invoke a denied tool — the call path rejects it before
    reaching the proxy."""
    policy = {"demo": {"blacklist": ["destroy_*"], "whitelist": []}}
    mw = router_mod._make_tool_filter_middleware(policy)

    called = []

    async def _call_next(_ctx):
        called.append(_ctx)
        return "should-not-reach"

    ctx = _FakeCallToolCtx("demo_destroy_all")

    with pytest.raises(ValueError, match="denied by plugin"):
        _run_async(mw.on_call_tool(ctx, _call_next))

    assert called == []  # call_next never ran for the denied tool


def test_middleware_passes_through_allowed_calls(tmp_path):
    """Allowed tools (and tools outside any policy namespace) must
    forward to ``call_next`` unchanged."""
    policy = {"demo": {"blacklist": ["destroy_*"], "whitelist": []}}
    mw = router_mod._make_tool_filter_middleware(policy)

    async def _call_next(ctx):
        return f"result:{ctx.message.name}"

    out = _run_async(mw.on_call_tool(_FakeCallToolCtx("demo_read"), _call_next))
    assert out == "result:demo_read"

    out2 = _run_async(mw.on_call_tool(_FakeCallToolCtx("router_help"), _call_next))
    assert out2 == "result:router_help"


def test_build_mcp_attaches_middleware_when_policy_present(tmp_path, monkeypatch):
    """End-to-end: when any mounted plugin declares a [tools] policy,
    build_mcp must install the filter middleware exactly once."""
    cfg = _tmp_cfg(tmp_path)
    _mk_policy_plugin(cfg, "guarded", blacklist=["danger"])
    state = router_mod.RouterState.bootstrap(cfg)

    # Stub fastmcp so we don't need the real server to count middlewares.
    import sys as _sys

    class _StubMCP:
        """Dual-signature fake for ``@mcp.tool`` and ``mcp.tool(name=...)``.

        The router uses both forms; a realistic stub has to accept both.
        """
        def __init__(self, *_a, **_kw) -> None:
            self.middlewares: list[object] = []
            self.mounts: list[tuple] = []
            self._tools: dict[str, object] = {}

        def tool(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if args and callable(args[0]):
                # Bare @mcp.tool usage — args[0] is the function.
                fn = args[0]
                self._tools[fn.__name__] = fn
                return fn
            name = kwargs.get("name")
            def deco(fn):  # type: ignore[no-untyped-def]
                self._tools[name or fn.__name__] = fn
                return fn
            return deco

        def mount(self, server, namespace):  # type: ignore[no-untyped-def]
            self.mounts.append((server, namespace))

        def add_middleware(self, mw):  # type: ignore[no-untyped-def]
            self.middlewares.append(mw)

    fake_fastmcp = type(_sys)("fastmcp")
    fake_fastmcp.FastMCP = _StubMCP
    fake_server = type(_sys)("fastmcp.server")
    fake_server.create_proxy = lambda cfg: ("proxy", cfg)
    # Real middleware import still needs to resolve, so expose the real
    # one from the already-installed package.
    import fastmcp.server.middleware as _real_mw
    fake_mw = type(_sys)("fastmcp.server.middleware")
    fake_mw.Middleware = _real_mw.Middleware
    fake_server.middleware = fake_mw

    monkeypatch.setitem(_sys.modules, "fastmcp", fake_fastmcp)
    monkeypatch.setitem(_sys.modules, "fastmcp.server", fake_server)
    monkeypatch.setitem(_sys.modules, "fastmcp.server.middleware", fake_mw)

    mcp = router_mod.build_mcp(state)

    assert len(mcp.middlewares) == 1
    assert len(mcp.mounts) == 1 and mcp.mounts[0][1] == "guarded"


# ---------------------------------------------------------------------------
# Fase 7a — [runtime].command/args alternative (uv run, uvx, node…)
# ---------------------------------------------------------------------------


def _mk_cmd_plugin(
    cfg: router_mod.RouterConfig,
    name: str,
    *,
    command: str,
    args: list[str],
) -> None:
    args_toml = ", ".join(f'"{a}"' for a in args)
    _mk_plugin(
        cfg.plugin_dir,
        name,
        f"""
        [plugin]
        name = "{name}"
        version = "1.0.0"

        [runtime]
        command = "{command}"
        args = [{args_toml}]

        [security]
        """,
    )


def test_runtime_command_payload_uses_declared_command(tmp_path):
    """``[runtime].command`` replaces the default ``sys.executable`` +
    entry launcher — exactly what's needed to delegate to ``uv run``,
    ``uvx`` or any other process manager."""
    cfg = _tmp_cfg(tmp_path)
    _mk_cmd_plugin(cfg, "prox", command="uv", args=["run", "homelab-proxmox-mcp"])
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "prox")

    payload = router_mod._plugin_mount_config(ps)
    server = payload["mcpServers"]["default"]

    assert server["command"] == "uv"
    assert server["args"] == ["run", "homelab-proxmox-mcp"]
    # cwd set to the plugin's own directory so ``uv run`` resolves the
    # right pyproject/venv without needing ``--directory``.
    assert Path(server["cwd"]) == (cfg.plugin_dir / "prox").resolve()
    assert "env" in server  # subprocess env scoping still applies


def test_runtime_command_substitutes_plugin_dir_placeholder(tmp_path):
    """``{plugin_dir}`` in args resolves to the absolute plugin path, so
    the manifest stays portable between checkout locations."""
    cfg = _tmp_cfg(tmp_path)
    _mk_cmd_plugin(
        cfg,
        "gpon",
        command="uv",
        args=["--directory", "{plugin_dir}", "run", "gpon-mcp"],
    )
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "gpon")

    payload = router_mod._plugin_mount_config(ps)
    args = payload["mcpServers"]["default"]["args"]

    plugin_dir = (cfg.plugin_dir / "gpon").resolve()
    assert args[0] == "--directory"
    assert Path(args[1]) == plugin_dir
    assert args[2:] == ["run", "gpon-mcp"]


def test_runtime_without_command_or_entry_raises(tmp_path):
    """A manifest with an empty ``[runtime]`` section cannot be mounted —
    the error must point both forms (entry + command) so plugin authors
    know their options."""
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "empty_runtime",
        """
        [plugin]
        name = "empty_runtime"
        version = "1.0.0"

        [runtime]
        # no entry, no command

        [security]
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "empty_runtime")

    with pytest.raises(ValueError, match="entry.*command"):
        router_mod._plugin_mount_config(ps)


def test_runtime_command_not_string_raises(tmp_path):
    """``command`` must be a string; a list would silently break the
    spawn. Fail loudly so the plugin shows up as ``error`` in status."""
    cfg = _tmp_cfg(tmp_path)
    _mk_plugin(
        cfg.plugin_dir,
        "badcmd",
        """
        [plugin]
        name = "badcmd"
        version = "1.0.0"

        [runtime]
        command = ["uv", "run"]

        [security]
        """,
    )
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "badcmd")

    with pytest.raises(ValueError, match="command must be a string"):
        router_mod._plugin_mount_config(ps)


# ---------------------------------------------------------------------------
# Fase 6d — subprocess env scoping (foreign-credential filter)
# ---------------------------------------------------------------------------


def _mk_creds_plugin(
    cfg: router_mod.RouterConfig, name: str, credential_refs: list[str]
) -> None:
    patterns = ", ".join(f'"{p}"' for p in credential_refs)
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
        credential_refs = [{patterns}]
        """,
    )
    (cfg.plugin_dir / name / "server.py").write_text(
        "# placeholder\n", encoding="utf-8"
    )


def test_plugin_env_inherits_ordinary_system_vars(tmp_path, monkeypatch):
    """PATH / APPDATA / HOME-style vars must not be filtered — the child
    Python would fail to start without them."""
    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "p1", credential_refs=["P1_*"])
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "p1")

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("APPDATA", "C:/Users/x/AppData")

    env = router_mod._plugin_subprocess_env(ps.manifest, [])

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["APPDATA"] == "C:/Users/x/AppData"


def test_plugin_env_forwards_own_credentials(tmp_path, monkeypatch):
    """A credential matching the plugin's own pattern must reach the
    subprocess — otherwise nothing works."""
    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "proxplug", credential_refs=["PROXMOX_*"])
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "proxplug")

    monkeypatch.setenv("PROXMOX_HOST", "192.0.2.1")
    monkeypatch.setenv("PROXMOX_TOKEN", "tok-ABC")

    all_patterns = router_mod._collect_all_credential_patterns(state)
    env = router_mod._plugin_subprocess_env(ps.manifest, all_patterns)

    assert env["PROXMOX_HOST"] == "192.0.2.1"
    assert env["PROXMOX_TOKEN"] == "tok-ABC"


def test_plugin_env_blocks_foreign_credentials(tmp_path, monkeypatch):
    """If plugin B claims ``PROXMOX_*`` and plugin A does not, A's
    subprocess must not see PROXMOX_* env — Layer 3 scoping across
    sibling subprocess plugins."""
    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "alpha", credential_refs=["ALPHA_*"])
    _mk_creds_plugin(cfg, "beta", credential_refs=["PROXMOX_*"])
    state = router_mod.RouterState.bootstrap(cfg)

    monkeypatch.setenv("PROXMOX_TOKEN", "secret")
    monkeypatch.setenv("ALPHA_TOKEN", "mine")

    alpha = next(p for p in state.report.plugins if p.manifest.name == "alpha")
    all_patterns = router_mod._collect_all_credential_patterns(state)
    env = router_mod._plugin_subprocess_env(alpha.manifest, all_patterns)

    assert "PROXMOX_TOKEN" not in env  # claimed by beta, not by alpha
    assert env["ALPHA_TOKEN"] == "mine"


def test_plugin_env_unclaimed_credential_passes_through(tmp_path, monkeypatch):
    """A credential-shaped var that NO plugin claims is harmless — keep
    the legacy behaviour of inheriting it. Only *foreign* claims block."""
    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "only", credential_refs=["ONLY_*"])
    state = router_mod.RouterState.bootstrap(cfg)

    monkeypatch.setenv("HOMELAB_DIR", "C:/homelab")  # looks credential-y, nobody claims

    ps = next(p for p in state.report.plugins if p.manifest.name == "only")
    all_patterns = router_mod._collect_all_credential_patterns(state)
    env = router_mod._plugin_subprocess_env(ps.manifest, all_patterns)

    assert env["HOMELAB_DIR"] == "C:/homelab"


def test_plugin_env_merges_vault_file_refs(tmp_path, monkeypatch):
    """A credential that lives only in secrets/*.md (not os.environ) must
    still reach the subprocess when the plugin declares a matching
    pattern — otherwise users storing secrets in the vault would find
    their plugins silently un-credentialed."""
    homelab_dir = tmp_path / "homelab"
    secrets_dir = homelab_dir / ".config" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "vault.md").write_text(
        "VAULTED_TOKEN=from-vault\n", encoding="utf-8"
    )

    # Point core.secrets at our fake homelab dir for this test.
    import core.secrets as _sec
    monkeypatch.setattr(_sec, "_SECRET_DIRS", [secrets_dir])
    monkeypatch.delenv("VAULTED_TOKEN", raising=False)

    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "vaultplug", credential_refs=["VAULTED_*"])
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "vaultplug")

    env = router_mod._plugin_subprocess_env(
        ps.manifest, router_mod._collect_all_credential_patterns(state)
    )

    assert env["VAULTED_TOKEN"] == "from-vault"


def test_plugin_mount_config_payload_includes_env(tmp_path):
    """The proxy config must carry an ``env`` dict now — without it
    FastMCP's create_proxy spawns with an empty env (Windows) or only
    inherits the launch shell's env, and most plugins break on import."""
    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "p1", credential_refs=["FOO_*"])
    state = router_mod.RouterState.bootstrap(cfg)
    ps = next(p for p in state.report.plugins if p.manifest.name == "p1")

    payload = router_mod._plugin_mount_config(ps)

    server = payload["mcpServers"]["default"]
    assert "env" in server
    assert isinstance(server["env"], dict)


def test_collect_all_credential_patterns_dedups(tmp_path):
    """If two plugins declare the same pattern, the union lists it once.
    Otherwise a tiny 'other - own' difference computation later would
    drop it from own scope incorrectly."""
    cfg = _tmp_cfg(tmp_path)
    _mk_creds_plugin(cfg, "a", credential_refs=["SHARED_*", "A_ONLY"])
    _mk_creds_plugin(cfg, "b", credential_refs=["SHARED_*", "B_ONLY"])
    state = router_mod.RouterState.bootstrap(cfg)

    patterns = router_mod._collect_all_credential_patterns(state)

    assert patterns.count("SHARED_*") == 1
    assert "A_ONLY" in patterns
    assert "B_ONLY" in patterns


def test_build_mcp_no_middleware_when_all_plugins_open(tmp_path, monkeypatch):
    """If no plugin declares a policy, the middleware is not attached —
    the common case pays zero cost."""
    cfg = _tmp_cfg(tmp_path)
    _mk_mountable_plugin(cfg, "open_plug")  # no [tools] section
    state = router_mod.RouterState.bootstrap(cfg)

    import sys as _sys

    class _StubMCP:
        def __init__(self, *_a, **_kw) -> None:
            self.middlewares: list[object] = []
            self.mounts: list[tuple] = []

        def tool(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if args and callable(args[0]):
                return args[0]
            def deco(fn):  # type: ignore[no-untyped-def]
                return fn
            return deco

        def mount(self, server, namespace):  # type: ignore[no-untyped-def]
            self.mounts.append((server, namespace))

        def add_middleware(self, mw):  # type: ignore[no-untyped-def]
            self.middlewares.append(mw)

    fake_fastmcp = type(_sys)("fastmcp")
    fake_fastmcp.FastMCP = _StubMCP
    fake_server = type(_sys)("fastmcp.server")
    fake_server.create_proxy = lambda cfg: object()
    monkeypatch.setitem(_sys.modules, "fastmcp", fake_fastmcp)
    monkeypatch.setitem(_sys.modules, "fastmcp.server", fake_server)

    mcp = router_mod.build_mcp(state)

    assert mcp.middlewares == []


# ---------------------------------------------------------------------------
# Fase 7e — plugin lifecycle meta-tools (wiring, audit, config gating)
# ---------------------------------------------------------------------------


def _install_mgmt_tools(tmp_path, *, audit_enabled=True, allow_install=False):
    """Build a live RouterState and return a _FakeMCP with the plugin
    lifecycle tools registered. Tests can invoke them from ``mcp.tools``."""
    cfg = _tmp_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, audit_enabled=audit_enabled, allow_plugin_install=allow_install
    )
    state = router_mod.RouterState.bootstrap(cfg)
    mcp = _FakeMCP()
    # Re-invoke the bit of build_mcp that registers router_* tools by
    # calling build_mcp with a stub fastmcp. Simpler: call the internals
    # directly — we only need the tools attached to our _FakeMCP.
    # To keep this lightweight we monkeypatch FastMCP to _FakeMCP and
    # call build_mcp.
    import sys as _sys
    fake_fastmcp = type(_sys)("fastmcp")
    fake_fastmcp.FastMCP = lambda *a, **kw: mcp  # type: ignore[assignment]
    fake_server = type(_sys)("fastmcp.server")
    fake_server.create_proxy = lambda cfg: object()
    import fastmcp.server.middleware as _real_mw
    fake_mw = type(_sys)("fastmcp.server.middleware")
    fake_mw.Middleware = _real_mw.Middleware
    fake_server.middleware = fake_mw
    import pytest as _pt
    with _pt.MonkeyPatch.context() as mp:
        mp.setitem(_sys.modules, "fastmcp", fake_fastmcp)
        mp.setitem(_sys.modules, "fastmcp.server", fake_server)
        mp.setitem(_sys.modules, "fastmcp.server.middleware", fake_mw)
        router_mod.build_mcp(state)
    return state, mcp


def test_install_plugin_tool_strict_returns_instruction(tmp_path):
    """The LLM should get a ``manual_instruction`` payload when the
    operator hasn't opted in — nothing touches disk."""
    state, mcp = _install_mgmt_tools(tmp_path, allow_install=False)
    fn = mcp.tools["router_install_plugin"]

    result = fn(source="github:acme/foo-mcp")

    assert result["executed"] is False
    assert "git clone" in result["command"]
    # Plugins dir untouched.
    assert list((state.cfg.plugin_dir).iterdir()) == []


def test_install_plugin_tool_rejects_execute_when_config_forbids(tmp_path):
    """``execute=True`` without ``allow_plugin_install`` must raise a
    PermissionError. That becomes an ``error:PermissionError`` in audit."""
    state, mcp = _install_mgmt_tools(tmp_path, allow_install=False)
    fn = mcp.tools["router_install_plugin"]

    with pytest.raises(PermissionError, match="allow_plugin_install"):
        fn(source="github:acme/foo-mcp", execute=True)


def test_install_plugin_tool_audit_logs(tmp_path, monkeypatch):
    """Every invocation produces one audit entry with the plugin name
    ``router`` and the tool name ``router_install_plugin`` — same
    contract as every other wrapped tool."""
    state, mcp = _install_mgmt_tools(tmp_path, audit_enabled=True)
    captured: list[dict] = []
    monkeypatch.setattr(
        router_mod.audit, "log_tool_call", lambda **kw: captured.append(kw)
    )
    fn = mcp.tools["router_install_plugin"]

    fn(source="github:acme/foo-mcp")

    assert len(captured) == 1
    entry = captured[0]
    assert entry["plugin"] == "router"
    assert entry["tool"] == "router_install_plugin"
    assert entry["status"] == "ok"
    # The source is fine to audit; values of credentials are the only
    # thing we hash/omit and this tool doesn't receive any.
    assert entry["args"]["source"] == "github:acme/foo-mcp"


def test_enable_plugin_tool_flips_manifest_flag(tmp_path):
    """``router_enable_plugin`` must actually edit the file so the
    next ``state.refresh()`` picks it up."""
    state, mcp = _install_mgmt_tools(tmp_path)
    plugin_dir = state.cfg.plugin_dir / "toggleable"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nname = "toggleable"\nversion = "1.0.0"\nenabled = false\n\n[security]\n',
        encoding="utf-8",
    )

    fn = mcp.tools["router_enable_plugin"]
    result = fn(name="toggleable")

    assert result["previous"] is False
    assert result["current"] is True
    text = (plugin_dir / "plugin.toml").read_text("utf-8")
    assert "enabled = true" in text


def test_list_plugins_tool_returns_full_listing(tmp_path):
    """``router_list_plugins`` must surface more than
    ``router_status`` — per-plugin detail including the enabled flag."""
    state, mcp = _install_mgmt_tools(tmp_path)
    for name, enabled in (("alpha", True), ("bravo", False)):
        d = state.cfg.plugin_dir / name
        d.mkdir(parents=True)
        flag = "true" if enabled else "false"
        (d / "plugin.toml").write_text(
            f'[plugin]\nname = "{name}"\nversion = "1.0.0"\nenabled = {flag}\n\n[security]\n',
            encoding="utf-8",
        )

    fn = mcp.tools["router_list_plugins"]
    result = fn()

    names = [p["name"] for p in result["plugins"]]
    assert names == ["alpha", "bravo"]
    alpha = next(p for p in result["plugins"] if p["name"] == "alpha")
    bravo = next(p for p in result["plugins"] if p["name"] == "bravo")
    assert alpha["enabled"] is True
    assert bravo["enabled"] is False


# ---------------------------------------------------------------------------
# Entry points: main() / run()
# ---------------------------------------------------------------------------

def test_main_dry_run_returns_zero(tmp_path, monkeypatch, capsys):
    """``main(["--dry-run"])`` carga config, hace bootstrap, imprime el
    reporte y sale con 0 sin arrancar el server FastMCP."""
    # Aislamos a tmp_path: sin router.toml → RouterConfig._defaults().
    monkeypatch.setattr(router_mod, "DEFAULT_CONFIG", tmp_path / "nope.toml")
    # Defaults apuntan a ROOT/plugins, ROOT/inventory — pueden no existir.
    # Forzamos un cfg controlado a través de RouterConfig.load.
    fake_cfg = router_mod.RouterConfig(
        profile="default",
        plugin_dir=tmp_path / "plugins",
        inventory_dir=tmp_path / "inventory",
        skills_dir=None,
        agents_dir=None,
        memory_backend="noop",
        memory_config={},
        strict_manifest=True,
        audit_enabled=False,  # menos ruido en stderr durante el test
        allow_plugin_install=False,
        state_path=tmp_path / "state.json",
        profile_path=tmp_path / "profiles" / "default.yaml",
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "inventory").mkdir()
    monkeypatch.setattr(
        router_mod.RouterConfig, "load",
        classmethod(lambda cls, path=None: fake_cfg),
    )

    rc = router_mod.main(["--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    # `format_report(state)` debe haberse imprimido.
    assert "MIMIR" in out.upper() or "plugins" in out.lower()


def test_main_returns_2_on_routerconfig_load_runtime_error(monkeypatch, capsys):
    """Si ``RouterConfig.load`` lanza ``RuntimeError`` (toml roto), el
    proceso emite el error a stderr y devuelve exit code 2."""
    def _boom(cls, path=None):
        raise RuntimeError("Could not parse router config at router.toml: bad")
    monkeypatch.setattr(router_mod.RouterConfig, "load", classmethod(_boom))

    rc = router_mod.main(["--dry-run"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "Could not parse" in err


def test_main_returns_2_on_inventory_error(tmp_path, monkeypatch, capsys):
    """Si ``RouterState.bootstrap`` lanza ``InventoryError`` (inventory
    inválido), devuelve exit code 2 y reporta a stderr."""
    from core.inventory import InventoryError
    fake_cfg = router_mod.RouterConfig(
        profile="default",
        plugin_dir=tmp_path / "plugins",
        inventory_dir=tmp_path / "inventory",
        skills_dir=None,
        agents_dir=None,
        memory_backend="noop",
        memory_config={},
        strict_manifest=True,
        audit_enabled=False,
        allow_plugin_install=False,
        state_path=tmp_path / "state.json",
        profile_path=tmp_path / "p.yaml",
    )
    monkeypatch.setattr(
        router_mod.RouterConfig, "load",
        classmethod(lambda cls, path=None: fake_cfg),
    )
    def _bad_bootstrap(cfg):
        raise InventoryError("inventory file malformed")
    monkeypatch.setattr(router_mod.RouterState, "bootstrap", _bad_bootstrap)

    rc = router_mod.main(["--dry-run"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "inventory" in err.lower()


def test_main_with_no_argv_reads_sys_argv(tmp_path, monkeypatch):
    """``main()`` sin ``argv`` debe usar ``sys.argv`` por defecto.

    Cubre la rama del argparse cuando ``argv is None``."""
    fake_cfg = router_mod.RouterConfig(
        profile="default",
        plugin_dir=tmp_path / "plugins",
        inventory_dir=tmp_path / "inventory",
        skills_dir=None,
        agents_dir=None,
        memory_backend="noop",
        memory_config={},
        strict_manifest=True,
        audit_enabled=False,
        allow_plugin_install=False,
        state_path=tmp_path / "state.json",
        profile_path=tmp_path / "p.yaml",
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "inventory").mkdir()
    monkeypatch.setattr(
        router_mod.RouterConfig, "load",
        classmethod(lambda cls, path=None: fake_cfg),
    )
    # Simular `python router.py --dry-run` (sin pasar argv explícito).
    monkeypatch.setattr(sys, "argv", ["router.py", "--dry-run"])

    rc = router_mod.main()

    assert rc == 0
