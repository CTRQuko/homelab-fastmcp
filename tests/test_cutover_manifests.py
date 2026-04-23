"""Load-checks the cutover plugin manifests under ``docs/cutover/manifests/``.

These files are the ground truth for Fase 7 extraction: they are the
exact ``plugin.toml`` blobs the operator will drop into each upstream
repo. Keeping them under a test makes sure a schema drift in the router
surfaces *before* someone commits a broken manifest to an external repo.

The tests are deliberately strict (``strict=True``) — the whole point of
having these in-repo is to exercise the same validation path Fase 7b
will hit when the real `plugins/` directory gets populated.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.loader import parse_manifest
import router as router_mod

ROOT = Path(__file__).resolve().parent.parent
CUTOVER_DIR = ROOT / "docs" / "cutover" / "manifests"
EXPECTED_PLUGINS = {"proxmox", "linux", "windows", "docker", "unifi", "uart", "gpon"}


def _manifest_paths() -> list[Path]:
    paths = sorted(CUTOVER_DIR.glob("*/plugin.toml"))
    assert paths, f"no cutover manifests under {CUTOVER_DIR}"
    return paths


def test_every_expected_plugin_has_a_manifest():
    """If a plugin goes missing from the cutover plan, the suite fails
    loudly — otherwise the cutover docs silently diverge from code."""
    present = {p.parent.name for p in _manifest_paths()}
    missing = EXPECTED_PLUGINS - present
    assert not missing, f"cutover plan missing manifests for: {sorted(missing)}"


@pytest.mark.parametrize("manifest_path", _manifest_paths(), ids=lambda p: p.parent.name)
def test_manifest_parses_strict(manifest_path):
    """Each cutover manifest must satisfy the same validation the router
    applies to ``plugins/*/plugin.toml`` at startup — required sections,
    name shape, version string, ``[security]`` block."""
    manifest = parse_manifest(manifest_path, strict=True)

    assert manifest.name == manifest_path.parent.name
    assert manifest.version, "version must not be empty"
    assert isinstance(manifest.security, dict), "[security] must exist"


@pytest.mark.parametrize("manifest_path", _manifest_paths(), ids=lambda p: p.parent.name)
def test_manifest_has_mountable_runtime(manifest_path):
    """The router cannot mount a manifest without either
    ``[runtime].command`` + ``args`` or ``[runtime].entry``. Catch the
    omission here so nobody publishes a broken manifest upstream."""
    manifest = parse_manifest(manifest_path, strict=True)
    runtime = manifest.runtime or {}
    assert (
        runtime.get("command") or runtime.get("entry")
    ), f"{manifest.name}: [runtime] lacks both 'command' and 'entry'"

    if runtime.get("command"):
        assert isinstance(runtime["command"], str)
        assert isinstance(runtime.get("args", []), list)


@pytest.mark.parametrize("manifest_path", _manifest_paths(), ids=lambda p: p.parent.name)
def test_manifest_subprocess_env_does_not_crash(manifest_path):
    """Feed the manifest's ``credential_refs`` through the real env
    builder. A bad regex or wrong type would blow up here instead of at
    router startup on a production machine."""
    manifest = parse_manifest(manifest_path, strict=True)
    env = router_mod._plugin_subprocess_env(manifest, all_credential_patterns=[])
    assert isinstance(env, dict)
    # Any credential-shaped value that slips in must at least be a string;
    # non-string env vars are an instant-fail when subprocess.Popen tries
    # to spawn the child.
    for k, v in env.items():
        assert isinstance(k, str)
        assert isinstance(v, str)


def test_credential_refs_are_lists_of_strings():
    """TOML quirk: a single-element array may type-check as a list of
    strings, but a typo like ``credential_refs = "PROXMOX_*"`` (string
    instead of list) passes the basic parse. Guard against it here —
    the scope check in ``core.secrets`` silently fails closed on a
    string that doesn't match, which is the *worst* failure mode."""
    for manifest_path in _manifest_paths():
        manifest = parse_manifest(manifest_path, strict=True)
        refs = manifest.security.get("credential_refs", [])
        assert isinstance(refs, list), (
            f"{manifest.name}: credential_refs must be a list, got {type(refs).__name__}"
        )
        for pat in refs:
            assert isinstance(pat, str), (
                f"{manifest.name}: credential_refs entries must be strings"
            )
