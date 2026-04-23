"""Tests for core.profile — plugin allowlist YAML gate."""
from __future__ import annotations

from core.profile import load_enabled_plugins


def test_missing_file_returns_none(tmp_path):
    assert load_enabled_plugins(tmp_path / "nope.yaml") is None


def test_missing_key_returns_none(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("other: value\n", encoding="utf-8")
    assert load_enabled_plugins(p) is None


def test_null_value_returns_empty_set(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("enabled_plugins: null\n", encoding="utf-8")
    assert load_enabled_plugins(p) == set()


def test_empty_list_returns_empty_set(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("enabled_plugins: []\n", encoding="utf-8")
    assert load_enabled_plugins(p) == set()


def test_list_returns_set(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("enabled_plugins:\n  - a\n  - b\n  - c\n", encoding="utf-8")
    assert load_enabled_plugins(p) == {"a", "b", "c"}


def test_non_list_returns_none(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("enabled_plugins: foo\n", encoding="utf-8")
    assert load_enabled_plugins(p) is None


def test_invalid_yaml_returns_none(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("enabled_plugins: [unclosed\n", encoding="utf-8")
    assert load_enabled_plugins(p) is None


def test_non_dict_top_level_returns_none(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert load_enabled_plugins(p) is None
