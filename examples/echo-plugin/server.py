"""Minimal example plugin server — zero dependencies, two tools.

Mount this as a subserver via the router's create_proxy path. Use it
as the skeleton for real plugins: copy the structure, swap the tool
bodies for your own logic, update plugin.toml with the right
credential_refs / requires sections.

Run standalone for a smoke test:

    python server.py
"""
from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP(name="echo")


@mcp.tool
def echo(text: str) -> str:
    """Return the input text unchanged. Useful as a liveness check."""
    return text


@mcp.tool
def reverse(text: str) -> str:
    """Return the input text reversed character by character."""
    return text[::-1]


if __name__ == "__main__":
    mcp.run(transport="stdio")
