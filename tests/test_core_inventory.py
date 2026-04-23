"""Tests for core.inventory YAML loader and API."""
from __future__ import annotations

import textwrap

import pytest

from core.inventory import (
    Inventory,
    InventoryError,
    append_host,
    append_service,
)


def _write(path, content):
    path.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture
def inventory_dir(tmp_path):
    _write(
        tmp_path / "hosts.yaml",
        """
        hosts:
          - name: host-a
            type: linux
            address: 192.0.2.10
            port: 22
            auth:
              method: ssh_key
              credential_ref: SSH_KEY_A
            tags: [prod, db]
          - name: hyp-1
            type: proxmox
            address: 192.0.2.20
            port: 8006
            tags: [prod]
        """,
    )
    _write(
        tmp_path / "services.yaml",
        """
        services:
          - name: svc-a
            type: generic
            host_ref: host-a
            port: 8443
        """,
    )
    return tmp_path


def test_load_basic(inventory_dir):
    inv = Inventory.load(inventory_dir)
    assert len(inv.get_hosts()) == 2
    assert len(inv.get_services()) == 1


def test_filter_by_type(inventory_dir):
    inv = Inventory.load(inventory_dir)
    assert [h.name for h in inv.get_hosts(type="proxmox")] == ["hyp-1"]


def test_filter_by_tag(inventory_dir):
    inv = Inventory.load(inventory_dir)
    assert [h.name for h in inv.get_hosts(tag="db")] == ["host-a"]


def test_filter_by_name(inventory_dir):
    inv = Inventory.load(inventory_dir)
    assert len(inv.get_hosts(name="host-a")) == 1
    assert len(inv.get_hosts(name="missing")) == 0


def test_missing_files_yield_empty(tmp_path):
    inv = Inventory.load(tmp_path)
    assert inv.get_hosts() == []
    assert inv.get_services() == []


def test_rejects_unknown_host_type(tmp_path):
    _write(
        tmp_path / "hosts.yaml",
        """
        hosts:
          - name: x
            type: martian
            address: 192.0.2.1
        """,
    )
    with pytest.raises(InventoryError, match="martian"):
        Inventory.load(tmp_path)


def test_rejects_service_with_unknown_host_ref(tmp_path):
    _write(tmp_path / "hosts.yaml", "hosts: []\n")
    _write(
        tmp_path / "services.yaml",
        """
        services:
          - name: svc
            type: generic
            host_ref: ghost
        """,
    )
    with pytest.raises(InventoryError, match="ghost"):
        Inventory.load(tmp_path)


def test_rejects_duplicate_host_name(tmp_path):
    _write(
        tmp_path / "hosts.yaml",
        """
        hosts:
          - name: dup
            type: linux
            address: 192.0.2.1
          - name: dup
            type: linux
            address: 192.0.2.2
        """,
    )
    with pytest.raises(InventoryError, match="duplicate"):
        Inventory.load(tmp_path)


def test_append_host_roundtrip(tmp_path):
    append_host(
        tmp_path,
        {"name": "new-host", "type": "linux", "address": "192.0.2.99"},
    )
    inv = Inventory.load(tmp_path)
    names = [h.name for h in inv.get_hosts()]
    assert names == ["new-host"]


def test_append_service_requires_existing_host(tmp_path):
    with pytest.raises(InventoryError):
        append_service(
            tmp_path,
            {"name": "svc", "type": "generic", "host_ref": "nobody"},
        )


def test_summary_counts_host_types(inventory_dir):
    inv = Inventory.load(inventory_dir)
    summary = inv.summary()
    assert summary["hosts_total"] == 2
    assert summary["services_total"] == 1
    assert summary["host_types"]["linux"] == 1
    assert summary["host_types"]["proxmox"] == 1
