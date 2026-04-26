"""Mimir — framework entry point.

The router loads core modules, declarative inventory, and any plugins
sitting under ``plugins/``. Once an MCP client connects over stdio,
Mimir exposes:

- ``router_*`` meta-tools so the LLM can guide the user through
  onboarding (add hosts/services/credentials, see what is missing).
- ``setup_<plugin>()`` dynamic tools for plugins waiting on
  requirements.
- The plugins themselves, mounted as subservers under their own
  namespace via FastMCP ``create_proxy``.
- Skills/agents discovered as ``.md`` files with frontmatter.

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

from core import audit, plugin_mgmt, secrets
from core.inventory import Inventory, InventoryError
from core.loader import LoadReport, reconcile, tool_allowed
from core.memory import MemoryBackend, load_backend
from core.profile import load_enabled_plugins
from core.skills import Skill, discover_agents, discover_skills

import fnmatch as _fnmatch

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
    allow_plugin_install: bool
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
            # Permissive install/remove of plugins is OFF by default — the
            # LLM is authorised to *propose* the command, not to run a clone
            # against arbitrary URLs. Operator flips this explicitly when
            # the trust boundary is understood.
            allow_plugin_install=bool(security.get("allow_plugin_install", False)),
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
            allow_plugin_install=False,
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
    lines.append(f"[mimir] router — profile: {cfg.profile}")
    lines.append(
        "[mimir] Core: inventory, secrets, audit, memory(" + state.memory.name + ")"
    )
    summary = state.inventory.summary()
    lines.append(
        f"[mimir] Inventory: {summary['hosts_total']} hosts, "
        f"{summary['services_total']} services"
    )
    lines.append(f"[mimir] Plugins discovered: {len(state.report.plugins)}")
    for p in state.report.plugins:
        lines.append(
            f"[mimir]   - {p.manifest.name} v{p.manifest.version}: {p.status}"
        )
        for req in p.missing:
            detail = ", ".join(f"{k}={v}" for k, v in req.detail.items())
            prompt = f" — {req.prompt}" if req.prompt else ""
            lines.append(f"[mimir]     Next ({req.kind}): {detail}{prompt}")
    if state.report.quarantined:
        lines.append(f"[mimir] Quarantined: {len(state.report.quarantined)}")
        for q in state.report.quarantined:
            lines.append(f"[mimir]   - {q.path.name}: {q.error}")
    if state.report.added:
        lines.append(f"[mimir] Added since last run: {', '.join(state.report.added)}")
    if state.report.removed:
        lines.append(f"[mimir] Removed since last run: {', '.join(state.report.removed)}")
    lines.append(
        f"[mimir] Skills: {len(state.skills)}  Agents: {len(state.agents)}"
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

    mcp = FastMCP("mimir")

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

    # -------- Plugin lifecycle meta-tools -----------------------------------
    # All four work on plugins/<name>/ directly. install/remove default to
    # "emit instruction" — the LLM hands the operator a command to run.
    # When `[security].allow_plugin_install = true` in router.toml the
    # operator has opted into the router doing the clone/remove itself.

    @mcp.tool
    def router_install_plugin(source: str, execute: bool = False) -> dict:
        """Install a plugin from a source.

        ``source`` accepts ``github:owner/repo``, a full https URL to a
        git repo, or an absolute local path. With ``execute=false``
        (default) the tool returns the exact command the operator
        should run. Setting ``execute=true`` is only honoured if
        ``[security].allow_plugin_install`` is ``true`` in
        ``router.toml`` — otherwise the call is rejected.

        After a successful install, restart Mimir so the plugin is
        discovered.
        """
        args = {"source": source, "execute": bool(execute)}

        def run() -> dict:
            if execute and not state.cfg.allow_plugin_install:
                raise PermissionError(
                    "execute=true requires [security].allow_plugin_install = true "
                    "in router.toml; current config forbids permissive installs"
                )
            if execute:
                # Security-relevant action: log a high-visibility warning
                # to stderr AND a dedicated audit entry so operators can
                # spot every executed install on a single grep, even when
                # the call returned status=ok.
                _log.warning(
                    "SECURITY: router executing plugin install (source=%s)", source
                )
                if state.cfg.audit_enabled:
                    audit.log_tool_call(
                        plugin="router",
                        tool="router_install_plugin",
                        args=args,
                        duration_ms=0.0,
                        status="security_event:plugin_install_executed",
                    )
            result = plugin_mgmt.install_plugin(
                source, state.cfg.plugin_dir, execute=bool(execute)
            )
            if result.get("executed"):
                state.refresh()
            return result

        return _timed("router_install_plugin", run, args)

    @mcp.tool
    def router_remove_plugin(name: str, execute: bool = False) -> dict:
        """Remove a plugin directory from ``plugins/``.

        Same two-mode contract as :func:`router_install_plugin`: without
        ``execute=true`` the tool returns the ``rm -rf`` command for
        the operator; with ``execute=true`` the router runs it directly
        and only if config allows.
        """
        args = {"name": name, "execute": bool(execute)}

        def run() -> dict:
            if execute and not state.cfg.allow_plugin_install:
                raise PermissionError(
                    "execute=true requires [security].allow_plugin_install = true "
                    "in router.toml; current config forbids permissive removes"
                )
            if execute:
                # Same security-event channel as router_install_plugin.
                _log.warning(
                    "SECURITY: router executing plugin remove (name=%s)", name
                )
                if state.cfg.audit_enabled:
                    audit.log_tool_call(
                        plugin="router",
                        tool="router_remove_plugin",
                        args=args,
                        duration_ms=0.0,
                        status="security_event:plugin_remove_executed",
                    )
            result = plugin_mgmt.remove_plugin(
                name, state.cfg.plugin_dir, execute=bool(execute)
            )
            if result.get("executed"):
                state.refresh()
            return result

        return _timed("router_remove_plugin", run, args)

    @mcp.tool
    def router_enable_plugin(name: str) -> dict:
        """Set ``[plugin].enabled = true`` in the plugin's manifest."""
        args = {"name": name}

        def run() -> dict:
            result = plugin_mgmt.set_plugin_enabled(
                name, state.cfg.plugin_dir, enabled=True
            )
            state.refresh()
            return result

        return _timed("router_enable_plugin", run, args)

    @mcp.tool
    def router_disable_plugin(name: str) -> dict:
        """Set ``[plugin].enabled = false`` in the plugin's manifest."""
        args = {"name": name}

        def run() -> dict:
            result = plugin_mgmt.set_plugin_enabled(
                name, state.cfg.plugin_dir, enabled=False
            )
            state.refresh()
            return result

        return _timed("router_disable_plugin", run, args)

    @mcp.tool
    def router_list_plugins() -> dict:
        """List every plugin discovered under ``plugins/`` with detail.

        Complements :func:`router_status` which only counts them.
        Includes quarantined entries so the operator can see what
        failed parsing without digging through logs.
        """
        def run() -> dict:
            return {"plugins": plugin_mgmt.list_plugins(state.cfg.plugin_dir)}

        return _timed("router_list_plugins", run, {})

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
            # Passing ``state`` so foreign credentials of sibling plugins
            # can be scoped out of this plugin's subprocess env.
            _mount_plugin(mcp, plugin_state, state)

    # Skills and agents as read-only tools.
    for skill in state.skills:
        _register_skill_tool(mcp, skill, state)
    for agent in state.agents:
        _register_agent_tool(mcp, agent, state)

    # Plugin [tools].whitelist/blacklist enforcement — installed as a
    # single middleware that consults a per-namespace policy dict. Only
    # attached if at least one mounted plugin actually declares a policy,
    # so the hot path stays empty for the common case.
    policy = _build_tool_policy(state)
    if policy:
        try:
            mcp.add_middleware(_make_tool_filter_middleware(policy))
            _log.info("tool policy middleware active for %d plugin(s)", len(policy))
        except Exception as exc:  # pragma: no cover — defensive
            _log.warning("failed to attach tool policy middleware: %s", exc)

    return mcp


def _plugin_subprocess_env(
    manifest, all_credential_patterns: list[str] | None = None
) -> dict[str, str]:
    """Build the env dict forwarded to a plugin subprocess.

    Two classes of variable are handled differently:

    - **System/runtime vars** (``PATH``, ``APPDATA``, ``HOME``, ``TEMP``,
      ``PYTHON*`` …): passed through unchanged. Without them the child
      Python interpreter cannot find caches, user dirs or site-packages.
    - **Credential-shaped vars** (``^[A-Z][A-Z0-9_]{2,}$`` containing at
      least one underscore): only forwarded if they match this plugin's
      ``[security].credential_refs`` patterns **or** are not claimed by
      any other plugin. This preserves Layer 3 scoping across sibling
      subprocess plugins — plugin A cannot see plugin B's tokens.

    Credentials that only live in ``secrets/*.md`` or ``.env`` (not in
    ``os.environ``) are resolved via :mod:`core.secrets` and merged in
    so subprocess plugins see the same view as in-process consumers of
    :func:`core.secrets.get_credential`.
    """
    own_patterns = list(manifest.security.get("credential_refs", []) or [])
    all_patterns = all_credential_patterns or own_patterns
    foreign_patterns = [p for p in all_patterns if p not in own_patterns]

    def _matches_own(key: str) -> bool:
        return any(_fnmatch.fnmatchcase(key, p) for p in own_patterns)

    def _matches_foreign(key: str) -> bool:
        return any(_fnmatch.fnmatchcase(key, p) for p in foreign_patterns)

    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if not value:
            continue
        if secrets._is_credential_key(key):
            # Credential-shaped. Keep only if ours, or not claimed by anyone.
            if _matches_own(key) or not _matches_foreign(key):
                env[key] = value
        else:
            # Ordinary system/runtime var — always inherit.
            env[key] = value

    # Pull in credentials stored only in secrets/*.md or .env so the
    # subprocess sees the same view the in-process vault would.
    for key, value in secrets.resolve_refs_matching(own_patterns).items():
        env.setdefault(key, value)

    return env


def _plugin_mount_config(plugin_state, all_credential_patterns=None) -> dict:  # type: ignore[no-untyped-def]
    """Build the ``create_proxy`` config dict for one plugin.

    Two manifest shapes are supported:

    1. **Simple Python script** — ``[runtime].entry = "server.py"``. The
       router launches ``sys.executable <plugin_dir>/server.py``.

    2. **Custom command** — ``[runtime].command = "uv"`` plus
       ``args = [...]``. Useful for ``uv run``, ``uvx``, ``node``, etc.
       The router invokes exactly that command with ``cwd`` set to the
       plugin's own directory. Occurrences of the literal
       ``{plugin_dir}`` in ``args`` are substituted for the resolved
       plugin path so authors don't need to hardcode absolute paths.

    Kept pure so tests can assert on the exact command/args without
    touching FastMCP or spawning a subprocess. Venv management, ``deps``
    install and ``python`` version matching are still deferred —
    documented in docs/framework-deferrals.md.

    ``all_credential_patterns`` is the union of ``credential_refs``
    declared by every loaded plugin, used to scope foreign credentials
    out of this plugin's subprocess env.
    """
    manifest = plugin_state.manifest
    runtime = manifest.runtime or {}
    env = _plugin_subprocess_env(manifest, all_credential_patterns)
    plugin_dir = str(manifest.path.resolve())

    command = runtime.get("command")
    if command:
        if not isinstance(command, str):
            raise ValueError(
                f"{manifest.name}: [runtime].command must be a string"
            )
        raw_args = runtime.get("args") or []
        if not isinstance(raw_args, list):
            raise ValueError(
                f"{manifest.name}: [runtime].args must be a list"
            )
        # {plugin_dir} substitution lets plugin authors keep the manifest
        # location-agnostic — the router fills in the real absolute path.
        args = [plugin_dir if a == "{plugin_dir}" else str(a) for a in raw_args]
        return {
            "mcpServers": {
                "default": {
                    "command": command,
                    "args": args,
                    "env": env,
                    "cwd": plugin_dir,
                }
            }
        }

    entry = runtime.get("entry")
    if not entry or not isinstance(entry, str):
        raise ValueError(
            f"{manifest.name}: [runtime] must declare either 'entry' "
            f"(Python script path) or 'command' (+ 'args')"
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
                "env": env,
            }
        }
    }


def _collect_all_credential_patterns(state: "RouterState") -> list[str]:
    """Union of ``credential_refs`` across every plugin the router knows.

    Quarantined entries don't contribute — their manifest never parsed, so
    they cannot declare credentials. Disabled / pending / error plugins do
    contribute: even if they're not serving right now, they've already
    claimed a pattern and we want to honour that scope for their siblings.
    """
    patterns: list[str] = []
    for ps in state.report.plugins:
        for pat in ps.manifest.security.get("credential_refs", []) or []:
            if pat not in patterns:
                patterns.append(pat)
    return patterns


def _mount_plugin(mcp, plugin_state, state=None) -> None:  # type: ignore[no-untyped-def]
    """Mount one plugin as a downstream MCP server under its own namespace.

    On any failure (missing entry, proxy creation, mount registration)
    downgrade the plugin status to ``error`` and record the cause on the
    :class:`PluginState`. The router keeps serving; `router_status()`
    shows the failure so the operator can react.
    """
    name = plugin_state.manifest.name
    try:
        all_patterns = (
            _collect_all_credential_patterns(state) if state is not None else None
        )
        config = _plugin_mount_config(plugin_state, all_patterns)
        from fastmcp.server import create_proxy  # lazy import

        mcp.mount(create_proxy(config), namespace=name)
        _log.info("mounted plugin '%s' (namespace=%s)", name, name)
    except Exception as exc:
        plugin_state.status = "error"
        plugin_state.error = f"{type(exc).__name__}: {exc}"
        _log.warning("plugin '%s' mount failed: %s", name, plugin_state.error)


def _build_tool_policy(state: "RouterState") -> dict[str, dict[str, Any]]:
    """Collect ``[tools]`` policy dicts from every plugin mounted OK.

    Plugins without any whitelist/blacklist drop out so the hot path in
    the middleware is proportional to plugins that actually filter.
    Quarantined/disabled/error plugins are not included — they don't
    expose tools in the first place.
    """
    policy: dict[str, dict[str, Any]] = {}
    for ps in state.report.plugins:
        if ps.status != "ok":
            continue
        tools_cfg = ps.manifest.tools or {}
        if tools_cfg.get("whitelist") or tools_cfg.get("blacklist"):
            policy[ps.manifest.name] = tools_cfg
    return policy


def _make_tool_filter_middleware(policy: dict[str, dict[str, Any]]):  # type: ignore[no-untyped-def]
    """Build a FastMCP middleware that enforces ``[tools]`` policy.

    Imported lazily so the router has no import-time dep on fastmcp when
    running ``--dry-run`` or from tests that stub the MCP layer. The
    middleware applies two checks:

    - ``on_list_tools``: the proxy forwards the full list from each
      mounted subserver; we drop denied names before the client sees
      them, so the LLM is never tempted to call a forbidden tool.
    - ``on_call_tool``: defence in depth — a client that calls a name
      anyway (cached list, malicious client, etc.) gets a clean
      ``ValueError`` instead of the denied tool running.

    Tools outside any known namespace (``router_*``, ``skill_*``, core)
    are always allowed — the policy is strictly about plugin-exposed
    tools.
    """
    from fastmcp.server.middleware import Middleware  # lazy import

    def _allowed(full_name: str) -> bool:
        for namespace, tools_cfg in policy.items():
            prefix = f"{namespace}_"
            if full_name.startswith(prefix):
                return tool_allowed(tools_cfg, full_name[len(prefix):])
        return True

    class _ToolPolicyMiddleware(Middleware):
        async def on_list_tools(self, context, call_next):  # type: ignore[no-untyped-def]
            tools = await call_next(context)
            return [t for t in tools if _allowed(t.name)]

        async def on_call_tool(self, context, call_next):  # type: ignore[no-untyped-def]
            name = getattr(context.message, "name", None)
            if name is not None and not _allowed(name):
                raise ValueError(
                    f"Tool '{name}' denied by plugin [tools] policy"
                )
            return await call_next(context)

    return _ToolPolicyMiddleware()


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
        print(f"[mimir] ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        state = RouterState.bootstrap(cfg)
    except InventoryError as exc:
        print(f"[mimir] ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_report(state))
    if dry_run:
        return 0

    try:
        mcp = build_mcp(state)
    except ImportError as exc:
        print(f"[mimir] ERROR: fastmcp not installed: {exc}", file=sys.stderr)
        return 2

    print("[mimir] Starting FastMCP server on stdio...", file=sys.stderr)
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("[mimir] KeyboardInterrupt — shutting down", file=sys.stderr)
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
