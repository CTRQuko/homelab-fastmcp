"""MCP server entry point for net-tools plugin.

v0.1.0 ships 5 Cloudflare DNS tools. Pi-hole + AdGuard submodules are
scaffolded (empty stubs) and will be wired in v0.2.0 / v0.3.0.

Mutation gating (real path, learned 2026-05-11): env var
``NETTOOLS_ALLOW_MUTATIONS=true`` must be set in the subprocess
environment. The plugin.toml ``[security].allow_mutations`` flag is
intent marker only — mimir-mcp core does not propagate it as env var.
"""
from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP

from .cloudflare.tools import (
    cloudflare_dns_create_record,
    cloudflare_dns_delete_record,
    cloudflare_dns_get_record,
    cloudflare_dns_list_records,
    cloudflare_dns_update_record,
)

log = logging.getLogger(__name__)

mcp = FastMCP("net-tools")


def _allow_mutations() -> bool:
    """Read the mutation gate from env (set by mimir vault propagation)."""
    raw = os.environ.get("NETTOOLS_ALLOW_MUTATIONS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _register_tools() -> None:
    """Register tools. Read-only always-on; mutating gated by env var."""
    # Cloudflare — read-only
    mcp.tool()(cloudflare_dns_list_records)
    mcp.tool()(cloudflare_dns_get_record)

    if _allow_mutations():
        log.info(
            "net-tools: NETTOOLS_ALLOW_MUTATIONS=true -> exposing 3 mutating tools"
        )
        mcp.tool()(cloudflare_dns_create_record)
        mcp.tool()(cloudflare_dns_update_record)
        mcp.tool()(cloudflare_dns_delete_record)
    else:
        log.info(
            "net-tools: NETTOOLS_ALLOW_MUTATIONS=false -> only 2 read-only "
            "tools exposed. Set NETTOOLS_ALLOW_MUTATIONS=true via "
            "router_add_credential to enable create/update/delete."
        )


def main() -> None:
    """CLI entry — runs the MCP server over stdio."""
    logging.basicConfig(
        level=os.environ.get("NETTOOLS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _register_tools()
    mcp.run()


if __name__ == "__main__":
    main()
