"""Smoke test the exact command OpenCode uses to launch Mimir.

Spawns ``uv run --directory C:\\homelab\\mcp-servers\\homelab-fastmcp mimir``
as a subprocess, connects with the FastMCP client, lists tools, calls
router_help, and reports. Mirrors the OpenCode mcp config exactly so a
green run here predicts OpenCode behaves the same.

Run with:
    .venv/Scripts/python.exe tests/manual/smoke_opencode_command.py
"""
from __future__ import annotations

import asyncio
import sys

from fastmcp import Client


async def main() -> int:
    client = Client(
        {
            "mcpServers": {
                "mimir": {
                    "command": "uv",
                    "args": [
                        "run",
                        "--directory",
                        r"C:\homelab\mcp-servers\homelab-fastmcp",
                        "mimir",
                    ],
                    "env": {},
                }
            }
        }
    )

    print("[smoke] connecting to mimir via the OpenCode command...")
    async with client:
        tools = await client.list_tools()
        print(f"[smoke] {len(tools)} tools exposed")
        for t in tools[:5]:
            print(f"        - {t.name}")
        if len(tools) > 5:
            print(f"        ... and {len(tools) - 5} more")

        print("[smoke] calling router_help()...")
        help_result = await client.call_tool("router_help", {})
        data = help_result.data
        assert data.get("name") == "mimir", f"unexpected: {data!r}"
        print(f"[smoke]   name={data.get('name')}")
        print(f"[smoke]   bootstrap tools: {len(data.get('available_bootstrap_tools', []))}")

        print("[smoke] calling router_status()...")
        status_result = await client.call_tool("router_status", {})
        sd = status_result.data
        print(f"[smoke]   memory_backend={sd.get('memory_backend')}")
        print(f"[smoke]   plugins={len(sd.get('plugins', []))}")
        print(f"[smoke]   inventory={sd.get('inventory')}")

    print("[smoke] OK — OpenCode command works end-to-end")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
