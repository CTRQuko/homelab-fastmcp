"""End-to-end smoke tests — spawn the real router and talk to it.

These are the tests Mimir's roadmap has been promising: actually start
``router.py`` as a subprocess, drive it through a real FastMCP
``Client`` over stdio, and assert that:

- The router exposes the meta-tools at all.
- ``router_help`` and ``router_status`` return well-formed payloads.
- A mounted example plugin's tools are visible and round-trip
  arguments correctly.

If any of this breaks, the framework is broken in a way unit tests
will not catch — the wiring between FastMCP, ``create_proxy``, the
middleware and the audit chain is exercised here for real.

Marked ``integration`` so the unit-test loop in CI keeps running fast;
this suite runs separately.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# These tests run a child Python process and load fastmcp client-side.
# Skip in environments where fastmcp isn't importable rather than
# blowing up — the unit suite doesn't depend on the client.
fastmcp = pytest.importorskip("fastmcp")
Client = fastmcp.Client


pytestmark = pytest.mark.integration


def _make_isolated_router_root(tmp_path: Path) -> Path:
    """Build a self-contained Mimir tree at ``tmp_path`` that mirrors
    the real one but with empty inventory and a single mounted plugin.

    Returns the path of ``router.py`` to invoke. The tree symlinks
    ``core/`` and ``router.py`` from the repo so the live code is
    exercised, but ``plugins/`` and ``inventory/`` and ``config/`` are
    fresh, so the test never reads or writes the operator's files.
    """
    # Symlink-friendly subset.
    for name in ("core", "router.py"):
        src = ROOT / name
        dst = tmp_path / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(src, dst)

    # Empty operator state — clean slate.
    (tmp_path / "plugins").mkdir()
    (tmp_path / "inventory").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "profiles").mkdir()

    # Default profile that explicitly enables every plugin we mount.
    (tmp_path / "profiles" / "default.yaml").write_text(
        "enabled_plugins:\n  - echo\n", encoding="utf-8"
    )

    # Drop the example plugin under plugins/echo so the router mounts
    # it during build_mcp.
    plugin_src = ROOT / "examples" / "echo-plugin"
    plugin_dst = tmp_path / "plugins" / "echo"
    shutil.copytree(plugin_src, plugin_dst)

    return tmp_path / "router.py"


def _client_for(router_path: Path) -> Client:
    """Build a FastMCP client that launches ``router.py`` over stdio."""
    return Client(
        {
            "mcpServers": {
                "mimir": {
                    "command": sys.executable,
                    "args": [str(router_path)],
                    "env": {},
                }
            }
        }
    )


@pytest.mark.asyncio
async def test_router_exposes_meta_tools_over_stdio(tmp_path):
    """The first contract: starting Mimir and listing tools must show
    the ``router_*`` family. If this fails, the entire stdio wiring is
    broken regardless of what the unit tests say."""
    router_path = _make_isolated_router_root(tmp_path)

    async with _client_for(router_path) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}

    expected = {
        "router_help",
        "router_status",
        "router_add_host",
        "router_add_service",
        "router_add_credential",
        "router_install_plugin",
        "router_remove_plugin",
        "router_enable_plugin",
        "router_disable_plugin",
        "router_list_plugins",
    }
    missing = expected - names
    assert not missing, f"router did not expose: {sorted(missing)}"


@pytest.mark.asyncio
async def test_router_help_returns_expected_shape(tmp_path):
    """``router_help`` is the first thing an LLM calls — its payload
    must contain at least name + purpose + a list of bootstrap tools.
    Lock the contract here so changes are deliberate."""
    router_path = _make_isolated_router_root(tmp_path)

    async with _client_for(router_path) as client:
        result = await client.call_tool("router_help", {})

    assert result.data is not None
    data = result.data
    assert data.get("name") == "mimir"
    assert "purpose" in data
    assert isinstance(data.get("available_bootstrap_tools"), list)
    assert "router_status" in data["available_bootstrap_tools"]


@pytest.mark.asyncio
async def test_router_status_reports_mounted_plugin(tmp_path):
    """With ``examples/echo-plugin`` symlinked under plugins/, the
    status payload must list it as discovered. Confirms the
    discovery → manifest parse → mount chain at runtime, not just at
    parse time."""
    router_path = _make_isolated_router_root(tmp_path)

    async with _client_for(router_path) as client:
        result = await client.call_tool("router_status", {})

    data = result.data
    assert data.get("memory_backend") == "noop"
    plugin_names = [p["name"] for p in data.get("plugins", [])]
    assert "echo" in plugin_names


@pytest.mark.asyncio
async def test_echo_plugin_tools_roundtrip(tmp_path):
    """The plugin mount path is what most aggregators get wrong. This
    test lifts the entire stack: router process → FastMCP proxy →
    echo subprocess → response → FastMCP proxy → router process →
    client. If echo_reverse can flip a string, the wiring is alive."""
    router_path = _make_isolated_router_root(tmp_path)

    async with _client_for(router_path) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "echo_echo" in names, names
        assert "echo_reverse" in names, names

        echoed = await client.call_tool("echo_echo", {"text": "mimir"})
        assert echoed.data == "mimir"

        reversed_ = await client.call_tool("echo_reverse", {"text": "mimir"})
        assert reversed_.data == "rimim"


@pytest.mark.asyncio
async def test_install_plugin_strict_mode_round_trip(tmp_path):
    """The LLM-guided install path: in strict mode the tool returns a
    manual_instruction payload. The plugins/ directory must stay
    untouched after the call — otherwise we're back to the "router
    runs arbitrary git clones" attack surface that the strict default
    explicitly avoids."""
    router_path = _make_isolated_router_root(tmp_path)
    plugins_dir = tmp_path / "plugins"
    before = set(plugins_dir.iterdir())

    async with _client_for(router_path) as client:
        result = await client.call_tool(
            "router_install_plugin",
            {"source": "github:acme/dummy-plugin"},
        )

    payload = result.data
    assert payload["executed"] is False
    assert payload["action"] == "manual_instruction"
    assert "git clone https://github.com/acme/dummy-plugin.git" in payload["command"]
    assert set(plugins_dir.iterdir()) == before, "plugins/ was modified in strict mode"


@pytest.mark.asyncio
async def test_list_plugins_surfaces_mounted(tmp_path):
    """router_list_plugins should expose the same plugin router_status
    counts, but with per-plugin detail (version, enabled, status). If
    the listing diverges from reality the LLM gets confused."""
    router_path = _make_isolated_router_root(tmp_path)

    async with _client_for(router_path) as client:
        result = await client.call_tool("router_list_plugins", {})

    plugins = result.data["plugins"]
    assert len(plugins) == 1
    echo = plugins[0]
    assert echo["name"] == "echo"
    assert echo["enabled"] is True
    assert echo["status"] == "ok"
    assert echo["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_audit_log_captures_meta_tool_calls(tmp_path):
    """Every router_* invocation must produce one line in the audit
    log. This test forces the framework to write to a known file
    (via MIMIR_AUDIT_LOG) and asserts the entries land there with
    the expected shape — proves the wiring at runtime, not just at
    import time."""
    router_path = _make_isolated_router_root(tmp_path)
    audit_log = tmp_path / "audit.jsonl"

    # Inject MIMIR_AUDIT_LOG into the subprocess env so it writes to
    # our temp file instead of the framework default.
    client = Client(
        {
            "mcpServers": {
                "mimir": {
                    "command": sys.executable,
                    "args": [str(router_path)],
                    "env": {"MIMIR_AUDIT_LOG": str(audit_log)},
                }
            }
        }
    )

    async with client:
        await client.call_tool("router_help", {})
        await client.call_tool("router_status", {})
        await client.call_tool("router_list_plugins", {})

    # The audit log is fire-and-forget — give it a beat to flush, the
    # router is already shut down by the time we read.
    assert audit_log.exists(), f"audit log not written at {audit_log}"
    lines = [
        line for line in audit_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    import json
    tool_names = [json.loads(line)["tool"] for line in lines]
    # Each invocation produces one entry; the three we made all show up.
    assert "router_help" in tool_names
    assert "router_status" in tool_names
    assert "router_list_plugins" in tool_names
    # Status field is "ok" for the happy path — error paths get
    # status="error:<ExceptionType>" but we didn't trigger any.
    statuses = {json.loads(line)["status"] for line in lines}
    assert statuses == {"ok"}
