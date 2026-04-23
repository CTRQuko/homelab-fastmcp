"""Declarative inventory layer for the router.

The framework itself is infra-agnostic. Users declare their infrastructure
in ``inventory/hosts.yaml`` and ``inventory/services.yaml`` (each of which
ships as a neutral ``.example`` template). Plugins never read these files
directly — they go through :func:`get_hosts`, :func:`get_services` and
:func:`get_credentials`, which the router enforces against plugin scope.

This MVP validates shape with a handful of explicit checks rather than a
full pydantic schema; the contract is small enough that the extra
dependency does not yet pay for itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.secrets import PluginContext, get_credential

_VALID_HOST_TYPES = {"linux", "windows", "macos", "proxmox", "network-device", "generic"}


class InventoryError(ValueError):
    """Raised on malformed inventory YAML."""


@dataclass(frozen=True)
class Auth:
    method: str
    credential_ref: str | None = None


@dataclass(frozen=True)
class Host:
    name: str
    type: str
    address: str
    port: int | None = None
    auth: Auth | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Service:
    name: str
    type: str
    host_ref: str
    port: int | None = None
    auth: Auth | None = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise InventoryError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InventoryError(f"{path}: top-level must be a mapping")
    return data


def _parse_auth(raw: dict | None) -> Auth | None:
    if not raw:
        return None
    if "method" not in raw:
        raise InventoryError("auth block missing 'method'")
    return Auth(method=str(raw["method"]), credential_ref=raw.get("credential_ref"))


def _parse_hosts(raw_list: list[dict]) -> list[Host]:
    out: list[Host] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise InventoryError(f"hosts[{idx}]: must be a mapping")
        for required in ("name", "type", "address"):
            if required not in item:
                raise InventoryError(f"hosts[{idx}]: missing '{required}'")
        name = str(item["name"])
        if name in seen:
            raise InventoryError(f"hosts[{idx}]: duplicate name '{name}'")
        seen.add(name)
        htype = str(item["type"])
        if htype not in _VALID_HOST_TYPES:
            raise InventoryError(
                f"hosts[{idx}] '{name}': type '{htype}' not in {sorted(_VALID_HOST_TYPES)}"
            )
        tags_raw = item.get("tags") or []
        if not isinstance(tags_raw, list):
            raise InventoryError(f"hosts[{idx}] '{name}': tags must be a list")
        out.append(
            Host(
                name=name,
                type=htype,
                address=str(item["address"]),
                port=item.get("port"),
                auth=_parse_auth(item.get("auth")),
                tags=tuple(str(t) for t in tags_raw),
            )
        )
    return out


def _parse_services(raw_list: list[dict], known_hosts: set[str]) -> list[Service]:
    out: list[Service] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise InventoryError(f"services[{idx}]: must be a mapping")
        for required in ("name", "type", "host_ref"):
            if required not in item:
                raise InventoryError(f"services[{idx}]: missing '{required}'")
        host_ref = str(item["host_ref"])
        if host_ref not in known_hosts:
            raise InventoryError(
                f"services[{idx}] '{item['name']}': host_ref '{host_ref}' not in hosts.yaml"
            )
        out.append(
            Service(
                name=str(item["name"]),
                type=str(item["type"]),
                host_ref=host_ref,
                port=item.get("port"),
                auth=_parse_auth(item.get("auth")),
            )
        )
    return out


class Inventory:
    """In-memory view of the user-declared infrastructure."""

    def __init__(self, hosts: list[Host], services: list[Service]):
        self._hosts = hosts
        self._services = services

    @classmethod
    def load(cls, inventory_dir: Path) -> "Inventory":
        hosts_data = _load_yaml(inventory_dir / "hosts.yaml")
        services_data = _load_yaml(inventory_dir / "services.yaml")
        hosts = _parse_hosts(hosts_data.get("hosts") or [])
        services = _parse_services(
            services_data.get("services") or [],
            known_hosts={h.name for h in hosts},
        )
        return cls(hosts, services)

    def get_hosts(
        self,
        type: str | None = None,
        tag: str | None = None,
        name: str | None = None,
    ) -> list[Host]:
        out = list(self._hosts)
        if type is not None:
            out = [h for h in out if h.type == type]
        if tag is not None:
            out = [h for h in out if tag in h.tags]
        if name is not None:
            out = [h for h in out if h.name == name]
        return out

    def get_services(
        self,
        type: str | None = None,
        host_ref: str | None = None,
    ) -> list[Service]:
        out = list(self._services)
        if type is not None:
            out = [s for s in out if s.type == type]
        if host_ref is not None:
            out = [s for s in out if s.host_ref == host_ref]
        return out

    def get_credentials(self, ref: str, ctx: PluginContext) -> str:
        """Thin pass-through to :mod:`core.secrets` keyed by scope."""
        return get_credential(ref, ctx)

    def summary(self) -> dict[str, int]:
        host_types: dict[str, int] = {}
        for h in self._hosts:
            host_types[h.type] = host_types.get(h.type, 0) + 1
        return {
            "hosts_total": len(self._hosts),
            "services_total": len(self._services),
            "host_types": host_types,
        }


# ---------------------------------------------------------------------------
# Writer for bootstrap tools (router_add_host / router_add_service)
# ---------------------------------------------------------------------------


def append_host(inventory_dir: Path, host: dict[str, Any]) -> None:
    """Append a host entry to ``hosts.yaml``.

    Used by the bootstrap tool ``router_add_host``. Creates the file with a
    ``hosts:`` top-level key if needed. Validates by re-parsing the result.
    """
    path = inventory_dir / "hosts.yaml"
    current = _load_yaml(path)
    hosts = current.get("hosts") or []
    hosts.append(host)
    current["hosts"] = hosts
    _parse_hosts(hosts)  # validate before writing
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(current, fh, sort_keys=False, allow_unicode=True)


def append_service(inventory_dir: Path, service: dict[str, Any]) -> None:
    path = inventory_dir / "services.yaml"
    current = _load_yaml(path)
    services = current.get("services") or []
    services.append(service)
    current["services"] = services
    hosts_data = _load_yaml(inventory_dir / "hosts.yaml")
    known = {h["name"] for h in (hosts_data.get("hosts") or []) if "name" in h}
    _parse_services(services, known_hosts=known)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(current, fh, sort_keys=False, allow_unicode=True)
