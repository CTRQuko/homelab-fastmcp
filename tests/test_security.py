"""Tests de seguridad: validación de inputs en tools nativas."""
import pytest

from native_tools.github import _validate_name, _validate_state
from native_tools.tailscale import _validate_device_id

# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------

def test_device_id_valid():
    _validate_device_id("abc123")
    _validate_device_id("my-device_1")


def test_device_id_invalid_empty():
    with pytest.raises(ValueError):
        _validate_device_id("")


def test_device_id_invalid_path_traversal():
    with pytest.raises(ValueError):
        _validate_device_id("../../../etc/passwd")


def test_device_id_invalid_special_chars():
    with pytest.raises(ValueError):
        _validate_device_id("device;rm -rf /")


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def test_name_valid():
    _validate_name("owner", "owner")
    _validate_name("my-repo_1.0", "repo")


def test_name_invalid_empty():
    with pytest.raises(ValueError):
        _validate_name("", "owner")


def test_name_invalid_path_traversal():
    with pytest.raises(ValueError):
        _validate_name("../../../etc/passwd", "owner")


def test_name_invalid_special_chars():
    with pytest.raises(ValueError):
        _validate_name("repo; rm -rf /", "repo")


def test_state_valid():
    _validate_state("open")
    _validate_state("closed")
    _validate_state("all")


def test_state_invalid():
    with pytest.raises(ValueError):
        _validate_state("hacked")
