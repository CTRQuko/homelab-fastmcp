"""Framework entry point — loads core modules, inventory and plugins.

This is the modular replacement for the legacy ``server.py``. It is wired
up alongside the legacy server while Fases 1–6 land; clients (Claude
Desktop, Hermes) keep pointing at ``server.py`` until Fase 8 cuts over.

Usage::

    uv run python router.py --dry-run          # print status, don't start MCP
    uv run python router.py                    # full startup (MVP stub)
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.inventory import Inventory, InventoryError
from core.loader import LoadReport, reconcile
from core.memory import MemoryBackend, load_backend

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "router.toml"


@dataclass
class RouterConfig:
    profile: str
    plugin_dir: Path
    inventory_dir: Path
    memory_backend: str
    memory_config: dict[str, Any]
    strict_manifest: bool
    state_path: Path

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
        return cls(
            profile=router.get("profile", "default"),
            plugin_dir=_abs(router.get("plugin_dir", "./plugins")),
            inventory_dir=_abs(router.get("inventory_dir", "./inventory")),
            memory_backend=backend,
            memory_config=backend_config,
            strict_manifest=bool(security.get("strict_manifest", True)),
            state_path=ROOT / "config" / ".last_state.json",
        )

    @classmethod
    def _defaults(cls) -> "RouterConfig":
        return cls(
            profile="default",
            plugin_dir=ROOT / "plugins",
            inventory_dir=ROOT / "inventory",
            memory_backend="noop",
            memory_config={},
            strict_manifest=True,
            state_path=ROOT / "config" / ".last_state.json",
        )


def _abs(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def format_report(
    cfg: RouterConfig, inventory: Inventory, memory: MemoryBackend, report: LoadReport
) -> str:
    lines: list[str] = []
    lines.append(f"[router] homelab-fastmcp framework — profile: {cfg.profile}")
    lines.append(
        "[router] Core: inventory, secrets, audit, memory(" + memory.name + ")"
    )
    summary = inventory.summary()
    lines.append(
        f"[router] Inventory: {summary['hosts_total']} hosts, "
        f"{summary['services_total']} services"
    )
    lines.append(f"[router] Plugins discovered: {len(report.plugins)}")
    for p in report.plugins:
        lines.append(
            f"[router]   - {p.manifest.name} v{p.manifest.version}: {p.status}"
        )
        for req in p.missing:
            detail = ", ".join(f"{k}={v}" for k, v in req.detail.items())
            prompt = f" — {req.prompt}" if req.prompt else ""
            lines.append(f"[router]     Next ({req.kind}): {detail}{prompt}")
    if report.added:
        lines.append(f"[router] Added since last run: {', '.join(report.added)}")
    if report.removed:
        lines.append(f"[router] Removed since last run: {', '.join(report.removed)}")
    return "\n".join(lines)


def run(dry_run: bool = False) -> int:
    try:
        cfg = RouterConfig.load()
    except RuntimeError as exc:
        print(f"[router] ERROR: {exc}", file=sys.stderr)
        return 2
    try:
        inventory = Inventory.load(cfg.inventory_dir)
    except InventoryError as exc:
        print(f"[router] ERROR: {exc}", file=sys.stderr)
        return 2
    memory = load_backend(cfg.memory_backend, cfg.memory_config)
    report = reconcile(
        cfg.plugin_dir, inventory, cfg.state_path, strict=cfg.strict_manifest
    )
    print(format_report(cfg, inventory, memory, report))
    if dry_run:
        return 0
    # MVP: full server mount is still handled by server.py / server.legacy.py.
    # The router binary reports state and exits cleanly. Fase 4 will wire the
    # FastMCP server with bootstrap tools here.
    print("[router] Dry-run complete. Full MCP mount deferred to a later phase.")
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
