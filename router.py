"""Framework entry point — loads core modules, inventory and plugins.

This is the modular replacement for the legacy ``server.py``. It is wired
up alongside the legacy server while Fases 1–6 land; clients (Claude
Desktop, Hermes) keep pointing at ``server.py`` until Fase 8 cuts over.

Usage::

    uv run python router.py --dry-run          # print status, don't start MCP
    uv run python router.py                    # full startup — serves over stdio
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import logging

from core import audit
from core.inventory import Inventory, InventoryError
from core.loader import LoadReport, reconcile
from core.memory import MemoryBackend, load_backend
from core.profile import load_enabled_plugins
from core.skills import Skill, discover_agents, discover_skills

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "router.toml"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class RouterConfig:
    profile: str
    plugin_dir: Path
    inventory_dir: Path
    skills_dir: Path | None
    agents_dir: Path | None
    memory_backend: str
    memory_config: dict[str, Any]
    strict_manifest: bool
    audit_enabled: bool
    state_path: Path
    profile_path: Path

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "RouterConfig":
        if not path.exists():
            return cls._defaults()
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise RuntimeError(f"Could not parse router config at {path.name}: {exc}") from exc
        router = data.get("router", {}) or {}
        memory = data.get("memory", {}) or {}
        security = data.get("security", {}) or {}
        backend = memory.get("backend", "noop")
        backend_config = memory.get(backend, {}) or {}
        profile_name = router.get("profile", "default")
        return cls(
            profile=profile_name,
            plugin_dir=_abs(router.get("plugin_dir", "./plugins")),
            inventory_dir=_abs(router.get("inventory_dir", "./inventory")),
            skills_dir=_abs_or_none(router.get("skills_dir")),
            agents_dir=_abs_or_none(router.get("agents_dir")),
            memory_backend=backend,
            memory_config=backend_config,
            strict_manifest=bool(security.get("strict_manifest", True)),
            audit_enabled=bool(security.get("audit_enabled", True)),
            state_path=ROOT / "config" / ".last_state.json",
            profile_path=ROOT / "profiles" / f"{profile_name}.yaml",
        )

    @classmethod
    def _defaults(cls) -> "RouterConfig":
        return cls(
            profile="default",
            plugin_dir=ROOT / "plugins",
            inventory_dir=ROOT / "inventory",
            skills_dir=None,
            agents_dir=None,
            memory_backend="noop",
            memory_config={},
            strict_manifest=True,
            audit_enabled=True,
            state_path=ROOT / "config" / ".last_state.json",
            profile_path=ROOT / "profiles" / "default.yaml",
        )


def _abs(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _abs_or_none(value: str | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return _abs(str(value))


# ---------------------------------------------------------------------------
# Mutable router state
# ---------------------------------------------------------------------------


@dataclass
class RouterState:
    """Live runtime state shared across all registered tools.

    The router holds this as a single object so the bootstrap tools can
    mutate inventory/vault and then ``refresh()`` to re-evaluate plugin
    requirements. Each MCP tool call reads the latest snapshot.
    """

    cfg: RouterConfig
    inventory: Inventory
    memory: MemoryBackend
    report: LoadReport
    profile_enabled: set[str] | None
    skills: list[Skill] = field(default_factory=list)
    agents: list[Skill] = field(default_factory=list)

    @classmethod
    def bootstrap(cls, cfg: RouterConfig) -> "RouterState":
        inventory = Inventory.load(cfg.inventory_dir)
        memory = load_backend(cfg.memory_backend, cfg.memory_config)
        report = reconcile(
            cfg.plugin_dir, inventory, cfg.state_path, strict=cfg.strict_manifest
        )
        profile_enabled = load_enabled_plugins(cfg.profile_path)
        _apply_profile_gate(report, profile_enabled)
        return cls(
            cfg=cfg,
            inventory=inventory,
            memory=memory,
            report=report,
            profile_enabled=profile_enabled,
            skills=discover_skills(cfg.skills_dir),
            agents=discover_agents(cfg.agents_dir),
        )

    def refresh(self) -> None:
        self.inventory = Inventory.load(self.cfg.inventory_dir)
        # Reload the profile gate live: editing profiles/<name>.yaml no longer
        # requires a restart to take effect.
        self.profile_enabled = load_enabled_plugins(self.cfg.profile_path)
        self.report = reconcile(
            self.cfg.plugin_dir,
            self.inventory,
            self.cfg.state_path,
            strict=self.cfg.strict_manifest,
        )
        _apply_profile_gate(self.report, self.profile_enabled)


def _apply_profile_gate(report: LoadReport, enabled: set[str] | None) -> None:
    """Mark plugins not listed in the active profile as disabled_by_profile."""
    if enabled is None:
        return
    for state in report.plugins:
        if state.manifest.name in enabled:
            continue
        # Preserve pre-existing 'disabled' (manifest said so) — profile
        # gate only downgrades plugins that would otherwise have activated.
        if state.status in {"disabled", "quarantined"}:
            continue
        state.status = "disabled_by_profile"
        state.missing = []


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(state: RouterState) -> str:
    cfg = state.cfg
    lines: list[str] = []
    lines.append(f"[router] homelab-fastmcp framework — profile: {cfg.profile}")
    lines.append(
        "[router] Core: inventory, secrets, audit, memory(" + state.memory.name + ")"
    )
    summary = state.inventory.summary()
    lines.append(
        f"[router] Inventory: {summary['hosts_total']} hosts, "
        f"{summary['services_total']} services"
    )
    lines.append(f"[router] Plugins discovered: {len(state.report.plugins)}")
    for p in state.report.plugins:
        lines.append(
            f"[router]   - {p.manifest.name} v{p.manifest.version}: {p.status}"
        )
        for req in p.missing:
            detail = ", ".join(f"{k}={v}" for k, v in req.detail.items())
            prompt = f" — {req.prompt}" if req.prompt else ""
            lines.append(f"[router]     Next ({req.kind}): {detail}{prompt}")
    if state.report.quarantined:
        lines.append(f"[router] Quarantined: {len(state.report.quarantined)}")
        for q in state.report.quarantined:
            lines.append(f"[router]   - {q.path.name}: {q.error}")
    if state.report.added:
        lines.append(f"[router] Added since last run: {', '.join(state.report.added)}")
    if state.report.removed:
        lines.append(f"[router] Removed since last run: {', '.join(state.report.removed)}")
    lines.append(
        f"[router] Skills: {len(state.skills)}  Agents: {len(state.agents)}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP wiring
# ---------------------------------------------------------------------------


def build_mcp(state: RouterState):  # type: ignore[no-untyped-def]
    """Build and return a configured FastMCP instance.

    Imported lazily so ``--dry-run`` works in environments where FastMCP is
    not installed (e.g. the unit test runner).
    """
    from fastmcp import FastMCP

    from core import bootstrap

    mcp = FastMCP("homelab-fastmcp-router")

    def _audit(tool: str, args: Any, duration_ms: float, status: str) -> None:
        if state.cfg.audit_enabled:
            audit.log_tool_call(
                plugin="router",
                tool=tool,
                args=args,
                duration_ms=duration_ms,
                status=status,
            )

    def _timed(tool_name: str, fn, args_for_audit):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        try:
            result = fn()
            _audit(tool_name, args_for_audit, (time.monotonic() - start) * 1000, "ok")
            return result
        except Exception as exc:
            _audit(
                tool_name,
                args_for_audit,
                (time.monotonic() - start) * 1000,
                f"error:{type(exc).__name__}",
            )
            raise

    @mcp.tool
    def router_help() -> dict:
        """Return an overview of the framework and what to do next."""
        return _timed("router_help", bootstrap.router_help, {})

    @mcp.tool
    def router_status() -> dict:
        """Current inventory, plugin states and pending setup steps."""
        return _timed(
            "router_status",
            lambda: bootstrap.router_status(
                state.inventory, state.report, state.cfg.memory_backend
            ),
            {},
        )

    @mcp.tool
    def router_add_host(
        name: str,
        type: str,
        address: str,
        port: int | None = None,
        credential_ref: str | None = None,
        auth_method: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Append a host to inventory/hosts.yaml (validated)."""
        args = {"name": name, "type": type, "address": address}

        def run() -> dict:
            result = bootstrap.router_add_host(
                state.cfg.inventory_dir,
                name=name,
                type=type,
                address=address,
                port=port,
                credential_ref=credential_ref,
                auth_method=auth_method,
                tags=tags,
            )
            state.refresh()
            return result

        return _timed("router_add_host", run, args)

    @mcp.tool
    def router_add_service(
        name: str,
        type: str,
        host_ref: str,
        port: int | None = None,
        credential_ref: str | None = None,
        auth_method: str | None = None,
    ) -> dict:
        """Append a service to inventory/services.yaml (validated)."""
        args = {"name": name, "type": type, "host_ref": host_ref}

        def run() -> dict:
            result = bootstrap.router_add_service(
                state.cfg.inventory_dir,
                name=name,
                type=type,
                host_ref=host_ref,
                port=port,
                credential_ref=credential_ref,
                auth_method=auth_method,
            )
            state.refresh()
            return result

        return _timed("router_add_service", run, args)

    @mcp.tool
    def router_add_credential(ref: str, value: str) -> dict:
        """Write a credential to the scoped vault, requires a plugin scope."""
        args = {"ref": ref}  # NEVER log value

        def run() -> dict:
            result = bootstrap.router_add_credential(ref, value, state.report)
            state.refresh()
            return result

        return _timed("router_add_credential", run, args)

    # Dynamic meta-tools: one setup_<plugin>() per plugin that still needs
    # configuration. They are a dead-simple wrapper that returns the same
    # ``missing`` payload ``router_status`` would expose, but scoped to one
    # plugin so the LLM can discover them via tools/list.
    for plugin_state in state.report.plugins:
        if plugin_state.status == "pending_setup":
            _register_setup_tool(mcp, state, plugin_state.manifest.name)
        elif plugin_state.status == "ok":
            # Mount the plugin as a downstream MCP server. Failures are
            # isolated so one broken plugin cannot hide healthy siblings
            # from the client — the error surfaces via status downgrade.
            _mount_plugin(mcp, plugin_state)

    # Skills and agents as read-only tools.
    for skill in state.skills:
        _register_skill_tool(mcp, skill, state)
    for agent in state.agents:
        _register_agent_tool(mcp, agent, state)

    return mcp


def _plugin_mount_config(plugin_state) -> dict:  # type: ignore[no-untyped-def]
    """Build the ``create_proxy`` config dict for one plugin.

    Kept pure so tests can assert on the exact command/args without
    touching FastMCP or spawning a subprocess. Current policy: use the
    router's own Python interpreter and invoke ``[runtime].entry`` as a
    script. Venv management, ``deps`` install and ``python`` version
    matching are deferred — documented in docs/framework-deferrals.md.
    """
    manifest = plugin_state.manifest
    runtime = manifest.runtime or {}
    entry = runtime.get("entry")
    if not entry or not isinstance(entry, str):
        raise ValueError(
            f"{manifest.name}: [runtime].entry is required to mount"
        )
    entry_path = (manifest.path / entry).resolve()
    if not entry_path.is_file():
        raise FileNotFoundError(
            f"{manifest.name}: [runtime].entry '{entry}' not found under {manifest.path}"
        )
    return {
        "mcpServers": {
            "default": {
                "command": sys.executable,
                "args": [str(entry_path)],
            }
        }
    }


def _mount_plugin(mcp, plugin_state) -> None:  # type: ignore[no-untyped-def]
    """Mount one plugin as a downstream MCP server under its own namespace.

    On any failure (missing entry, proxy creation, mount registration)
    downgrade the plugin status to ``error`` and record the cause on the
    :class:`PluginState`. The router keeps serving; `router_status()`
    shows the failure so the operator can react.
    """
    name = plugin_state.manifest.name
    try:
        config = _plugin_mount_config(plugin_state)
        from fastmcp.server import create_proxy  # lazy import

        mcp.mount(create_proxy(config), namespace=name)
        _log.info("mounted plugin '%s' (namespace=%s)", name, name)
    except Exception as exc:
        plugin_state.status = "error"
        plugin_state.error = f"{type(exc).__name__}: {exc}"
        _log.warning("plugin '%s' mount failed: %s", name, plugin_state.error)


def _setup_payload(state: "RouterState", plugin_name: str) -> dict:
    """Build the setup_<plugin>() response from *live* router state.

    Extracted from the MCP tool wrapper so unit tests can exercise the
    live-state behaviour without requiring fastmcp.
    """
    live = next(
        (p for p in state.report.plugins if p.manifest.name == plugin_name),
        None,
    )
    if live is None:
        return {
            "plugin": plugin_name,
            "status": "not_found",
            "missing": [],
            "next_tool_hint": (
                "Plugin no longer discoverable — call router_status() for "
                "the current picture."
            ),
        }
    return {
        "plugin": plugin_name,
        "version": live.manifest.version,
        "status": live.status,
        "missing": [
            {"kind": r.kind, "detail": r.detail, "prompt": r.prompt}
            for r in live.missing
        ],
        "next_tool_hint": (
            "Setup complete — the plugin is active. Call router_status() "
            "for the full inventory."
            if live.status == "ok"
            else "Call router_add_host / router_add_credential with the "
            "details each missing requirement describes."
        ),
    }


def _register_setup_tool(mcp, state: "RouterState", plugin_name: str) -> None:  # type: ignore[no-untyped-def]
    """Register setup_<plugin>() that reads live state on each invocation.

    MCP stdio cannot de-register tools mid-session, so a tool registered
    because the plugin was pending at bootstrap stays exposed forever. The
    tool must therefore report the *current* status — not a snapshot taken
    at registration time — so the LLM sees ``ok`` once setup completes.
    """
    tool_name = f"setup_{plugin_name}"

    def _setup() -> dict:
        start = time.monotonic()
        try:
            result = _setup_payload(state, plugin_name)
            if state.cfg.audit_enabled:
                audit.log_tool_call(
                    plugin=plugin_name,
                    tool=tool_name,
                    args={},
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="ok",
                )
            return result
        except Exception as exc:
            if state.cfg.audit_enabled:
                audit.log_tool_call(
                    plugin=plugin_name,
                    tool=tool_name,
                    args={},
                    duration_ms=(time.monotonic() - start) * 1000,
                    status=f"error:{type(exc).__name__}",
                )
            raise

    _setup.__name__ = tool_name
    _setup.__doc__ = (
        f"Report what is still needed before plugin '{plugin_name}' activates."
    )
    mcp.tool(name=tool_name)(_setup)


def _register_skill_tool(mcp, skill: Skill, state: RouterState) -> None:  # type: ignore[no-untyped-def]
    tool_name = f"skill_{skill.name}"

    def _fn() -> dict:
        start = time.monotonic()
        try:
            result = {
                "name": skill.name,
                "description": skill.description,
                "body": skill.body,
                "path": str(skill.path),
            }
            if state.cfg.audit_enabled:
                audit.log_tool_call(
                    plugin="skills",
                    tool=tool_name,
                    args={},
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="ok",
                )
            return result
        except Exception:
            if state.cfg.audit_enabled:
                audit.log_tool_call(
                    plugin="skills",
                    tool=tool_name,
                    args={},
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="error",
                )
            raise

    _fn.__name__ = tool_name
    _fn.__doc__ = skill.description
    mcp.tool(name=tool_name)(_fn)


def _register_agent_tool(mcp, agent: Skill, state: RouterState) -> None:  # type: ignore[no-untyped-def]
    tool_name = f"agent_{agent.name}"

    def _fn() -> dict:
        start = time.monotonic()
        try:
            result = {
                "name": agent.name,
                "description": agent.description,
                "body": agent.body,
                "path": str(agent.path),
                "note": (
                    "Agent invocation is not yet executed by the router — this "
                    "tool returns the agent definition so the caller can apply it."
                ),
            }
            if state.cfg.audit_enabled:
                audit.log_tool_call(
                    plugin="agents",
                    tool=tool_name,
                    args={},
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="ok",
                )
            return result
        except Exception:
            if state.cfg.audit_enabled:
                audit.log_tool_call(
                    plugin="agents",
                    tool=tool_name,
                    args={},
                    duration_ms=(time.monotonic() - start) * 1000,
                    status="error",
                )
            raise

    _fn.__name__ = tool_name
    _fn.__doc__ = agent.description
    mcp.tool(name=tool_name)(_fn)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def run(dry_run: bool = False) -> int:
    try:
        cfg = RouterConfig.load()
    except RuntimeError as exc:
        print(f"[router] ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        state = RouterState.bootstrap(cfg)
    except InventoryError as exc:
        print(f"[router] ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_report(state))
    if dry_run:
        return 0

    try:
        mcp = build_mcp(state)
    except ImportError as exc:
        print(f"[router] ERROR: fastmcp not installed: {exc}", file=sys.stderr)
        return 2

    print("[router] Starting FastMCP server on stdio...", file=sys.stderr)
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("[router] KeyboardInterrupt — shutting down", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="router")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print router status and exit without starting the MCP server.",
    )
    args = parser.parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
