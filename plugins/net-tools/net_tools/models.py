"""Pydantic schemas shared across submodules.

Stable contract — bumps require version bump.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Cloudflare DNS
# ---------------------------------------------------------------------------

DnsRecordType = Literal["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA"]


class DnsRecord(BaseModel):
    """Cloudflare DNS record, normalized to the fields we care about."""

    id: str
    name: str
    type: DnsRecordType
    content: str
    proxied: bool = False
    ttl: int = 1  # 1 = "automatic" (CF translates to 300s internally)
    comment: Optional[str] = None
    priority: Optional[int] = Field(
        default=None,
        description="Only meaningful for MX/SRV records.",
    )


class DnsRecordList(BaseModel):
    """Output of ``cloudflare_dns_list_records``."""

    zone_id: str
    zone_name: str
    records: list[DnsRecord]
    count: int


class DnsRecordMutation(BaseModel):
    """Output of create/update/delete ops."""

    id: str
    name: str
    action: Literal["created", "updated", "deleted", "already_absent"]


# ---------------------------------------------------------------------------
# Pi-hole
# ---------------------------------------------------------------------------

class PiholeStatus(BaseModel):
    host_ref: str
    address: str
    version: str
    ftl_version: str
    blocking: Literal["enabled", "disabled", "failed", "unknown"]
    queries_today: int = 0
    queries_blocked_today: int = 0
    uptime_seconds: int = 0


class CustomDnsEntry(BaseModel):
    """Local DNS record in Pi-hole (`custom_dns` in dnsmasq config)."""

    domain: str
    ip: str  # IPv4 or IPv6


# ---------------------------------------------------------------------------
# AdGuard
# ---------------------------------------------------------------------------

class Rewrite(BaseModel):
    """AdGuard Home DNS rewrite (one ``answer`` per domain entry)."""

    domain: str
    answer: str  # IPv4, IPv6, or CNAME target


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class QueryLogEntry(BaseModel):
    """One row of DNS query log (used by both Pi-hole and AdGuard tools)."""

    ts: str  # ISO 8601 UTC
    domain: str
    client: str
    type: str
    status: str
    reply: Optional[str] = None
