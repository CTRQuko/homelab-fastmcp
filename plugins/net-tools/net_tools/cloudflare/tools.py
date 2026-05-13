"""Cloudflare DNS MCP tools — 5 tools en v0.1.0.

NOTA: las funciones se registran en ``net_tools/server.py`` con el
decorator ``@mcp.tool()``. Aquí solo son pure functions con la
validación + business logic + envelope-shape return.

Mutation gating: ``create``, ``update``, ``delete`` requieren
``NETTOOLS_ALLOW_MUTATIONS=true`` en el env del subprocess. El gate
real se aplica al registrar (server.py), aquí ya están filtradas si
el flag está OFF.

ADR-0002 del homelab: ``proxied`` SIEMPRE False. Hard-coded en
``_validate_proxied``. Override solo via flag ``NETTOOLS_ALLOW_PROXIED=true``
para entornos donde el operador quiera usar Cloudflare como CDN.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from ..errors import (
    IdempotencyError,
    NetToolsError,
    NotFoundError,
    ValidationError,
    envelope_error,
)
from ..models import DnsRecord, DnsRecordList, DnsRecordMutation
from .client import CloudflareClient

_VALID_TYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA"}
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_IPV6_HINT_RE = re.compile(r"^[0-9a-fA-F:]+$")
_FQDN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9_]|[a-z0-9_][a-z0-9_-]*[a-z0-9_])(?:\.(?:[a-z0-9_]|[a-z0-9_][a-z0-9_-]*[a-z0-9_]))+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_proxied(proxied: bool) -> None:
    """Reject ``proxied=True`` unless explicit override env var is set."""
    if not proxied:
        return
    if os.environ.get("NETTOOLS_ALLOW_PROXIED", "").strip().lower() in {"1", "true", "yes"}:
        return
    raise ValidationError(
        "proxied=True rejected by default (homelab ADR-0002). Cloudflare "
        "proxy breaks split-DNS and ACME DNS-01. To override globally, set "
        "NETTOOLS_ALLOW_PROXIED=true in the vault."
    )


def _validate_type(type_: str) -> None:
    if type_ not in _VALID_TYPES:
        raise ValidationError(
            f"type {type_!r} not in {sorted(_VALID_TYPES)}"
        )


def _validate_content_for_type(type_: str, content: str) -> None:
    """Best-effort shape check (full validation done by Cloudflare server-side)."""
    if type_ == "A":
        if not _IPV4_RE.match(content):
            raise ValidationError(f"A record content must be IPv4 dotted, got {content!r}")
    elif type_ == "AAAA":
        if ":" not in content or not _IPV6_HINT_RE.match(content):
            raise ValidationError(f"AAAA record content must be IPv6, got {content!r}")
    elif type_ == "CNAME":
        if not _FQDN_RE.match(content):
            raise ValidationError(f"CNAME content must be FQDN, got {content!r}")
    # TXT/MX/SRV/etc: no client-side validation; trust CF


def _validate_ttl(ttl: int) -> None:
    if ttl == 1:
        return  # "automatic"
    if not (60 <= ttl <= 86400):
        raise ValidationError(
            f"ttl must be 1 (automatic) or in [60, 86400], got {ttl}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_record(raw: dict) -> dict:
    """Convert CF API result row to our DnsRecord shape."""
    return DnsRecord(
        id=raw["id"],
        name=raw["name"],
        type=raw["type"],
        content=raw["content"],
        proxied=raw.get("proxied", False),
        ttl=raw.get("ttl", 1),
        comment=raw.get("comment"),
        priority=raw.get("priority"),
    ).model_dump()


def _zone_name(client: CloudflareClient) -> str:
    """Cached zone name lookup. Single network call per tool invocation."""
    return client.get_zone().get("name", "")


# ---------------------------------------------------------------------------
# Tool 1 — list (always-on)
# ---------------------------------------------------------------------------

def cloudflare_dns_list_records(
    name_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    proxied_only: Optional[bool] = None,
) -> dict:
    """List DNS records of the configured zone, with optional filters.

    Args:
        name_filter: substring match (case-insensitive) against record name.
        type_filter: exact-match record type ("A", "AAAA", "CNAME", ...).
        proxied_only: if True, return only proxied records; if False,
            only non-proxied; if None, both.

    Returns ``{ok, data}`` with ``DnsRecordList`` payload.
    """
    try:
        if type_filter is not None and type_filter not in _VALID_TYPES:
            raise ValidationError(
                f"type_filter {type_filter!r} not in {sorted(_VALID_TYPES)}"
            )

        client = CloudflareClient.from_env()
        # Server-side filter by type when given (saves bandwidth).
        raw = client.list_records(type=type_filter)

        # Client-side filter by name (substring) and proxied.
        filtered = []
        nf_lower = name_filter.lower() if name_filter else None
        for row in raw:
            if nf_lower and nf_lower not in row.get("name", "").lower():
                continue
            if proxied_only is True and not row.get("proxied", False):
                continue
            if proxied_only is False and row.get("proxied", False):
                continue
            filtered.append(_normalize_record(row))

        return {
            "ok": True,
            "data": DnsRecordList(
                zone_id=client.zone_id,
                zone_name=_zone_name(client),
                records=[DnsRecord(**r) for r in filtered],
                count=len(filtered),
            ).model_dump(),
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 2 — get by name (always-on)
# ---------------------------------------------------------------------------

def cloudflare_dns_get_record(
    name: str,
    type: Optional[str] = None,
) -> dict:
    """Look up one record by FQDN (and optionally type to disambiguate).

    Returns the matching record. If multiple records exist for the
    same name+type combo (round-robin DNS), returns a ``validation``
    error with the list of candidates' IDs — caller picks specifically.
    """
    try:
        if not name or not name.strip():
            raise ValidationError("name is required")
        client = CloudflareClient.from_env()
        raw = client.list_records(name=name, type=type)
        if not raw:
            raise NotFoundError(
                f"no record with name={name!r} type={type!r}"
            )
        if len(raw) > 1:
            types_found = sorted({r.get("type") for r in raw})
            raise ValidationError(
                f"ambiguous: {len(raw)} records match name={name!r}; "
                f"types found: {types_found}. Specify `type=` or pick "
                f"by id from list_records."
            )
        return {"ok": True, "data": _normalize_record(raw[0])}
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 3 — create (gated)
# ---------------------------------------------------------------------------

def cloudflare_dns_create_record(
    name: str,
    type: str,
    content: str,
    ttl: int = 1,
    proxied: bool = False,
    comment: Optional[str] = None,
    priority: Optional[int] = None,
    confirm: bool = False,
) -> dict:
    """Create a DNS record. Mutating — requires ``confirm=True``.

    Idempotency: if a record with same (name, type) already exists,
    returns ``{ok: False, error_type: "idempotency"}`` with the
    existing id in ``context.existing_id``. Caller chooses to
    ``cloudflare_dns_update_record`` instead.
    """
    try:
        if not confirm:
            raise ValidationError(
                "confirm=True required for mutation. Re-call with confirm=True."
            )
        _validate_type(type)
        _validate_content_for_type(type, content)
        _validate_ttl(ttl)
        _validate_proxied(proxied)
        if type == "MX" and priority is None:
            raise ValidationError("priority is required for MX records")

        client = CloudflareClient.from_env()

        # Idempotency check.
        existing = client.list_records(name=name, type=type)
        if existing:
            raise IdempotencyError(
                f"record name={name!r} type={type!r} already exists",
                {"existing_id": existing[0]["id"], "existing_content": existing[0].get("content")},
            )

        payload: dict[str, Any] = {
            "name": name,
            "type": type,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
        }
        if comment:
            payload["comment"] = comment
        if priority is not None:
            payload["priority"] = priority

        result = client.create_record(payload)
        return {
            "ok": True,
            "data": DnsRecordMutation(
                id=result["id"],
                name=result["name"],
                action="created",
            ).model_dump(),
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 4 — update (gated)
# ---------------------------------------------------------------------------

def cloudflare_dns_update_record(
    record_id: str,
    content: Optional[str] = None,
    ttl: Optional[int] = None,
    proxied: Optional[bool] = None,
    comment: Optional[str] = None,
    confirm: bool = False,
) -> dict:
    """Update a DNS record. Mutating — requires ``confirm=True``.

    At least one of ``content``/``ttl``/``proxied``/``comment`` must
    be non-None. PATCH semantics (CF accepts partial updates).
    """
    try:
        if not confirm:
            raise ValidationError(
                "confirm=True required for mutation. Re-call with confirm=True."
            )
        if all(v is None for v in (content, ttl, proxied, comment)):
            raise ValidationError(
                "no-op: pass at least one of content/ttl/proxied/comment"
            )
        if ttl is not None:
            _validate_ttl(ttl)
        if proxied is not None:
            _validate_proxied(proxied)

        client = CloudflareClient.from_env()
        # Resolve current to validate type-vs-content if content changed.
        current = client.get_record(record_id)
        rtype = current.get("type", "")

        if content is not None and rtype:
            _validate_content_for_type(rtype, content)

        payload: dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if ttl is not None:
            payload["ttl"] = ttl
        if proxied is not None:
            payload["proxied"] = proxied
        if comment is not None:
            payload["comment"] = comment

        result = client.update_record(record_id, payload)
        return {
            "ok": True,
            "data": DnsRecordMutation(
                id=result["id"],
                name=result["name"],
                action="updated",
            ).model_dump(),
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 5 — delete (gated, idempotente)
# ---------------------------------------------------------------------------

def cloudflare_dns_delete_record(
    record_id: str,
    confirm: bool = False,
) -> dict:
    """Delete a record. Idempotent (404 → action="already_absent")."""
    try:
        if not confirm:
            raise ValidationError(
                "confirm=True required for mutation. Re-call with confirm=True."
            )
        client = CloudflareClient.from_env()
        try:
            result = client.delete_record(record_id)
        except NotFoundError:
            return {
                "ok": True,
                "data": DnsRecordMutation(
                    id=record_id, name="", action="already_absent",
                ).model_dump(),
            }
        return {
            "ok": True,
            "data": DnsRecordMutation(
                id=result.get("id", record_id),
                name="",
                action="deleted",
            ).model_dump(),
        }
    except NetToolsError as exc:
        return envelope_error(exc)
