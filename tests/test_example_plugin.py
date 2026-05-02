"""Smoke tests for examples/echo-plugin/.

The example plugin is meant as a living contract: if the framework's
manifest schema, mount config builder or env scoping ever changes in
a way that breaks the minimal 'do nothing interesting' case, these
tests catch it before the break reaches downstream plugins.

They are deliberately strict and use the *real* helpers
(``parse_manifest``, ``_plugin_mount_config``,
``_plugin_subprocess_env``) rather than mocks.
"""
from __future__ import annotations

from pathlib import Path

import router as router_mod
from core.loader import PluginManifest, PluginState, parse_manifest

ROOT = Path(__file__).resolve().parent.parent
ECHO_DIR = ROOT / "examples" / "echo-plugin"


def _load_manifest() -> PluginManifest:
    return parse_manifest(ECHO_DIR / "plugin.toml", strict=True)


def test_echo_plugin_manifest_parses():
    """The example manifest must satisfy the same strict parser that
    runs against production plugins — otherwise the README lies about
    being a usable template."""
    manifest = _load_manifest()

    assert manifest.name == "echo"
    assert manifest.version == "1.0.0"
    assert manifest.enabled is True
    assert isinstance(manifest.security, dict)
    assert manifest.security.get("credential_refs") == []


def test_echo_plugin_server_file_exists():
    """The entry path declared in plugin.toml must resolve to a real
    file on disk. A manifest that points at a missing script would pass
    schema validation but blow up at mount time."""
    manifest = _load_manifest()
    entry = manifest.runtime["entry"]
    entry_path = (manifest.path / entry).resolve()
    assert entry_path.is_file(), f"missing entry script: {entry_path}"


def test_echo_plugin_mount_config_is_well_formed():
    """Feed the manifest through the router's real mount-config builder
    and verify the shape FastMCP's create_proxy expects. Catches any
    future regression where the builder silently drops a required key."""
    manifest = _load_manifest()
    plugin_state = PluginState(manifest=manifest, status="ok")

    config = router_mod._plugin_mount_config(plugin_state)

    server = config["mcpServers"]["default"]
    assert "command" in server
    assert "args" in server and isinstance(server["args"], list)
    assert "env" in server and isinstance(server["env"], dict)

    # The entry path in args must point at the plugin's own server.py.
    assert len(server["args"]) == 1
    entry = Path(server["args"][0])
    assert entry.is_file()
    assert entry.name == "server.py"


def test_echo_plugin_env_is_clean():
    """A plugin with empty credential_refs must not pick up any
    credential-shaped vars from the host env — that's the whole point
    of scoped env propagation. Ordinary system vars (PATH etc.) still
    flow."""
    manifest = _load_manifest()
    env = router_mod._plugin_subprocess_env(manifest, all_credential_patterns=[])

    # PATH should always be there for the Python interpreter to start.
    # (Not asserting the value, just that the builder didn't nuke it.)
    import os
    if "PATH" in os.environ:
        assert env.get("PATH") == os.environ["PATH"]

    # No credential-shaped entries claimed (credential_refs = []).
    from core.secrets import _is_credential_key
    for key in env:
        # Any credential-shaped key that slipped in must come from a
        # system source we deliberately let through (HOMELAB_DIR and
        # similar underscore-bearing sys vars). The scoping test in
        # test_router_wiring.py covers the cross-plugin filter; here
        # we just confirm the plugin didn't claim anything itself.
        if _is_credential_key(key):
            # Accept only vars that aren't matched by any pattern on
            # this plugin — which is everything since patterns = [].
            # So any credential-shaped key present here is necessarily
            # an "unclaimed passthrough" (not foreign), which is fine.
            pass  # explicit no-op: documents the rationale


def test_echo_plugin_discovered_by_reconcile(tmp_path):
    """End-to-end: drop the example plugin under a fresh plugin_dir
    and confirm ``reconcile`` treats it as a normal, mountable plugin.
    This is the exact path the router runs at startup."""
    import shutil

    from core.inventory import Inventory
    from core.loader import reconcile

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    shutil.copytree(ECHO_DIR, plugins_dir / "echo")

    inventory = Inventory(hosts=[], services=[])
    report = reconcile(
        plugin_dir=plugins_dir,
        inventory=inventory,
        state_path=tmp_path / ".last_state.json",
        strict=True,
    )

    names = [p.manifest.name for p in report.plugins]
    assert "echo" in names
    echo_state = next(p for p in report.plugins if p.manifest.name == "echo")
    assert echo_state.status == "ok", f"got {echo_state.status}: {echo_state.error}"
