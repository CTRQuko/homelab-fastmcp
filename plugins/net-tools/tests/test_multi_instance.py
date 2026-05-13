"""Tests for net_tools.multi_instance resolver."""
from __future__ import annotations

import pytest

from net_tools.errors import ValidationError
from net_tools.multi_instance import list_known_instances, resolve_instance


def test_resolve_instance_happy_path(monkeypatch):
    monkeypatch.setenv("ADGUARD_L1_HOST", "http://10.0.1.14:3000")
    monkeypatch.setenv("ADGUARD_L1_USER", "admin")
    monkeypatch.setenv("ADGUARD_L1_PASSWORD", "s3cret")
    info = resolve_instance("ADGUARD", "l1")
    assert info["host"] == "http://10.0.1.14:3000"
    assert info["user"] == "admin"
    assert info["password"] == "s3cret"
    assert info["token"] is None


def test_resolve_instance_case_insensitive_host_ref(monkeypatch):
    monkeypatch.setenv("ADGUARD_VPS_HOST", "http://1.2.3.4:3000")
    monkeypatch.setenv("ADGUARD_VPS_USER", "admin")
    monkeypatch.setenv("ADGUARD_VPS_PASSWORD", "x")
    info = resolve_instance("ADGUARD", "vps")
    assert info["host"] == "http://1.2.3.4:3000"
    # Same with uppercase
    info2 = resolve_instance("ADGUARD", "VPS")
    assert info2["host"] == info["host"]


def test_resolve_instance_strips_trailing_slash_from_host(monkeypatch):
    monkeypatch.setenv("ADGUARD_L1_HOST", "http://10.0.1.14:3000/")
    monkeypatch.setenv("ADGUARD_L1_USER", "admin")
    monkeypatch.setenv("ADGUARD_L1_PASSWORD", "x")
    info = resolve_instance("ADGUARD", "l1")
    assert info["host"] == "http://10.0.1.14:3000"


def test_resolve_instance_missing_host_raises(monkeypatch):
    monkeypatch.delenv("ADGUARD_NOPE_HOST", raising=False)
    with pytest.raises(ValidationError, match="ADGUARD_NOPE_HOST"):
        resolve_instance("ADGUARD", "nope")


def test_resolve_instance_empty_host_ref():
    with pytest.raises(ValidationError, match="host_ref is required"):
        resolve_instance("ADGUARD", "")


def test_resolve_instance_invalid_host_ref():
    with pytest.raises(ValidationError, match="invalid"):
        resolve_instance("ADGUARD", "../../etc/passwd")


def test_resolve_instance_optional_fields_none_when_unset(monkeypatch):
    monkeypatch.setenv("ADGUARD_X_HOST", "http://x:3000")
    monkeypatch.delenv("ADGUARD_X_USER", raising=False)
    monkeypatch.delenv("ADGUARD_X_PASSWORD", raising=False)
    monkeypatch.delenv("ADGUARD_X_TOKEN", raising=False)
    info = resolve_instance("ADGUARD", "x")
    assert info["host"] == "http://x:3000"
    assert info["user"] is None
    assert info["password"] is None
    assert info["token"] is None


def test_list_known_instances(monkeypatch):
    # Clear any leaking ADGUARD_ keys first
    for k in list(__import__("os").environ.keys()):
        if k.startswith("ADGUARD_") and k.endswith("_HOST"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ADGUARD_L1_HOST", "x")
    monkeypatch.setenv("ADGUARD_L2_HOST", "y")
    monkeypatch.setenv("ADGUARD_VPS_HOST", "z")
    # Unrelated env vars must not match
    monkeypatch.setenv("ADGUARD_L1_USER", "admin")  # not a HOST suffix
    monkeypatch.setenv("UNRELATED_X_HOST", "no")
    instances = list_known_instances("ADGUARD")
    assert set(instances) == {"l1", "l2", "vps"}


def test_list_known_instances_empty(monkeypatch):
    for k in list(__import__("os").environ.keys()):
        if k.startswith("PIHOLE_"):
            monkeypatch.delenv(k, raising=False)
    assert list_known_instances("PIHOLE") == []
