"""Tests for ``core.plugin_mgmt`` — the lifecycle ops the router exposes.

The pure functions live in ``core.plugin_mgmt`` specifically so the
testing surface stays independent from FastMCP and the router. Every
wire-level concern (audit wrapping, config-driven gating) is covered by
``tests/test_router_wiring.py`` — here we just validate the behaviour.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core import plugin_mgmt
from core.plugin_mgmt import (
    PluginMgmtError,
    install_plugin,
    list_plugins,
    parse_install_source,
    remove_plugin,
    set_plugin_enabled,
)


def _mk_plugin(plugins_dir: Path, name: str, *, enabled: bool = True) -> Path:
    d = plugins_dir / name
    d.mkdir(parents=True, exist_ok=True)
    enabled_str = "true" if enabled else "false"
    (d / "plugin.toml").write_text(
        textwrap.dedent(
            f"""\
            [plugin]
            name = "{name}"
            version = "1.0.0"
            enabled = {enabled_str}

            [runtime]
            entry = "server.py"

            [security]
            credential_refs = []
            """
        ),
        encoding="utf-8",
    )
    (d / "server.py").write_text("# placeholder\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# parse_install_source
# ---------------------------------------------------------------------------


def test_parse_source_github_shortform():
    """``github:owner/repo`` is the primary LLM-friendly form — it must
    round-trip into a git url with the repo name as the default target."""
    spec = parse_install_source("github:acme/my-plugin")
    assert spec["kind"] == "git"
    assert spec["url"] == "https://github.com/acme/my-plugin.git"
    assert spec["target_name"] == "my-plugin"


def test_parse_source_strips_mcp_suffix_from_target():
    """Repos following the mcp-plugin-* convention usually end in -mcp.
    We strip that tail so the local dir is just the theme name."""
    spec = parse_install_source("github:acme/foo-mcp")
    assert spec["target_name"] == "foo"
    spec_url = parse_install_source("https://github.com/acme/foo-mcp.git")
    assert spec_url["target_name"] == "foo"


def test_parse_source_rejects_relative_path():
    """Local sources must be absolute. A relative path is ambiguous —
    relative to what? We fail loudly instead of guessing."""
    with pytest.raises(PluginMgmtError, match="absolute path"):
        parse_install_source("./some/dir")


def test_parse_source_rejects_empty():
    with pytest.raises(PluginMgmtError, match="must not be empty"):
        parse_install_source("")


def test_parse_source_local_path_round_trip(tmp_path):
    src = tmp_path / "external-plugin"
    src.mkdir()
    spec = parse_install_source(str(src))
    assert spec["kind"] == "copy"
    assert spec["target_name"] == src.name


def test_parse_source_local_path_must_exist(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(PluginMgmtError, match="does not exist"):
        parse_install_source(str(missing))


# ---------------------------------------------------------------------------
# install_plugin — strict (instruction only)
# ---------------------------------------------------------------------------


def test_install_strict_returns_instruction_not_executed(tmp_path):
    """Without ``execute=True`` the router must not touch disk. The LLM
    hands the command to the operator."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    result = install_plugin("github:acme/my-plugin", plugins_dir)

    assert result["action"] == "manual_instruction"
    assert result["executed"] is False
    assert "git clone https://github.com/acme/my-plugin.git" in result["command"]
    # Crucially: nothing was created.
    assert not (plugins_dir / "my-plugin").exists()


def test_install_strict_rejects_bad_name(tmp_path):
    """A GitHub shortform that would derive an illegal plugin name (say,
    with capitals) must fail at validation, not silently rename."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    with pytest.raises(PluginMgmtError, match="invalid plugin name"):
        # BadName has capital letters which are not allowed.
        install_plugin("github:Acme/BadName", plugins_dir)


def test_install_strict_rejects_traversal_in_source(tmp_path):
    """Even with hyphens allowed in names, a slash or dots must still
    be rejected to prevent path escape."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    with pytest.raises(PluginMgmtError, match="invalid plugin name"):
        install_plugin("github:a/b", plugins_dir, name_override="../escape")


# ---------------------------------------------------------------------------
# install_plugin — permissive (local copy path, no network)
# ---------------------------------------------------------------------------


def test_install_permissive_local_copy(tmp_path):
    """Local path + execute=True copies the directory into plugins/."""
    src = tmp_path / "src-plugin"
    src.mkdir()
    (src / "plugin.toml").write_text(
        '[plugin]\nname = "cp_plug"\nversion = "1.0.0"\n\n[security]\n',
        encoding="utf-8",
    )
    plugins_dir = tmp_path / "plugins"

    result = install_plugin(str(src), plugins_dir, execute=True)

    assert result["executed"] is True
    assert (plugins_dir / src.name / "plugin.toml").is_file()


def test_install_permissive_refuses_to_overwrite(tmp_path):
    """If the target already exists we fail before clobbering. The
    operator must remove it first."""
    src = tmp_path / "src-plugin"
    src.mkdir()
    (src / "plugin.toml").write_text(
        '[plugin]\nname = "dup"\nversion = "1.0.0"\n\n[security]\n',
        encoding="utf-8",
    )
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / src.name).mkdir(parents=True)

    with pytest.raises(PluginMgmtError, match="already exists"):
        install_plugin(str(src), plugins_dir, execute=True)


# ---------------------------------------------------------------------------
# remove_plugin
# ---------------------------------------------------------------------------


def test_remove_strict_returns_instruction(tmp_path):
    plugins_dir = tmp_path / "plugins"
    _mk_plugin(plugins_dir, "target")

    result = remove_plugin("target", plugins_dir)

    assert result["executed"] is False
    assert "rm -rf" in result["command"]
    # Still there.
    assert (plugins_dir / "target").exists()


def test_remove_permissive_deletes(tmp_path):
    plugins_dir = tmp_path / "plugins"
    _mk_plugin(plugins_dir, "target")

    result = remove_plugin("target", plugins_dir, execute=True)

    assert result["executed"] is True
    assert not (plugins_dir / "target").exists()


def test_remove_nonexistent_raises(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    with pytest.raises(PluginMgmtError, match="not found"):
        remove_plugin("ghost", plugins_dir)


def test_remove_traversal_rejected(tmp_path):
    """``name = "../secret"`` must not escape. Validated by the regex,
    but the test locks the behaviour in."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    with pytest.raises(PluginMgmtError, match="invalid plugin name"):
        remove_plugin("../secret", plugins_dir)


# ---------------------------------------------------------------------------
# set_plugin_enabled
# ---------------------------------------------------------------------------


def test_toggle_disable_then_enable(tmp_path):
    plugins_dir = tmp_path / "plugins"
    _mk_plugin(plugins_dir, "togg", enabled=True)

    r1 = set_plugin_enabled("togg", plugins_dir, enabled=False)
    assert r1["previous"] is True and r1["current"] is False
    text_after = (plugins_dir / "togg" / "plugin.toml").read_text("utf-8")
    assert "enabled = false" in text_after

    r2 = set_plugin_enabled("togg", plugins_dir, enabled=True)
    assert r2["previous"] is False and r2["current"] is True
    text_after_2 = (plugins_dir / "togg" / "plugin.toml").read_text("utf-8")
    assert "enabled = true" in text_after_2


def test_toggle_preserves_rest_of_manifest(tmp_path):
    """The edit should be surgical — comments, spacing, and every other
    key stay exactly as written by the plugin author."""
    plugins_dir = tmp_path / "plugins"
    d = plugins_dir / "preserve"
    d.mkdir(parents=True)
    original = textwrap.dedent(
        """\
        # Top-of-file comment
        [plugin]
        name = "preserve"
        version = "1.0.0"
        enabled = true
        # trailing comment

        [runtime]
        entry = "server.py"

        [security]
        credential_refs = ["FOO_*"]
        """
    )
    (d / "plugin.toml").write_text(original, encoding="utf-8")

    set_plugin_enabled("preserve", plugins_dir, enabled=False)

    after = (d / "plugin.toml").read_text("utf-8")
    assert "# Top-of-file comment" in after
    assert "# trailing comment" in after
    assert 'credential_refs = ["FOO_*"]' in after
    assert "enabled = false" in after
    assert "enabled = true" not in after


def test_toggle_inserts_enabled_when_absent(tmp_path):
    """A minimal manifest without an explicit ``enabled =`` line should
    still be toggleable — the line is inserted right after ``[plugin]``."""
    plugins_dir = tmp_path / "plugins"
    d = plugins_dir / "stub"
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(
        '[plugin]\nname = "stub"\nversion = "1.0.0"\n\n[security]\n',
        encoding="utf-8",
    )

    set_plugin_enabled("stub", plugins_dir, enabled=False)

    after = (d / "plugin.toml").read_text("utf-8")
    assert "enabled = false" in after
    # Sanity: the existing content is still around.
    assert 'name = "stub"' in after
    assert "[security]" in after


def test_toggle_refuses_invalid_manifest(tmp_path):
    """Editing a broken manifest would silently overwrite the operator's
    diagnostic output. Refuse instead."""
    plugins_dir = tmp_path / "plugins"
    d = plugins_dir / "broken"
    d.mkdir(parents=True)
    (d / "plugin.toml").write_text(
        '[plugin]\nname = "broken"\n# missing version\n[security]\n',
        encoding="utf-8",
    )
    with pytest.raises(PluginMgmtError, match="refusing to edit invalid manifest"):
        set_plugin_enabled("broken", plugins_dir, enabled=False)


# ---------------------------------------------------------------------------
# list_plugins
# ---------------------------------------------------------------------------


def test_list_returns_sorted_entries_with_manifest_detail(tmp_path):
    plugins_dir = tmp_path / "plugins"
    _mk_plugin(plugins_dir, "bravo", enabled=True)
    _mk_plugin(plugins_dir, "alpha", enabled=False)

    listing = list_plugins(plugins_dir)

    assert [p["name"] for p in listing] == ["alpha", "bravo"]
    alpha = next(p for p in listing if p["name"] == "alpha")
    assert alpha["enabled"] is False
    assert alpha["status"] == "ok"
    assert alpha["version"] == "1.0.0"


def test_list_surfaces_quarantined_plugins(tmp_path):
    """A broken manifest shows up in the listing — silently skipping it
    would hide the very problem the operator needs to fix."""
    plugins_dir = tmp_path / "plugins"
    _mk_plugin(plugins_dir, "good")
    bad = plugins_dir / "bad"
    bad.mkdir()
    (bad / "plugin.toml").write_text("not even toml", encoding="utf-8")

    listing = list_plugins(plugins_dir)

    assert any(p["status"] == "quarantined" for p in listing)
    bad_entry = next(p for p in listing if p["path"].endswith("bad"))
    assert bad_entry["status"] == "quarantined"
    assert "error" in bad_entry


def test_list_empty_plugins_dir(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    assert list_plugins(plugins_dir) == []


def test_list_missing_plugins_dir(tmp_path):
    """A missing plugins/ directory isn't an error — the router just
    hasn't had any plugins installed yet."""
    assert list_plugins(tmp_path / "does-not-exist") == []
