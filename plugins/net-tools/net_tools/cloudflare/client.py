"""Cloudflare API v4 client (DNS zone scope).

Single-zone client. The plugin reads ``CLOUDFLARE_ZONE_ID`` from
environment at construction; multi-zone management is out of v0.1.0
scope.

Reference: https://developers.cloudflare.com/api/operations/dns-records-for-a-zone-list-dns-records
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..errors import AuthError, UpstreamError, ValidationError
from ..http_client import HttpClient

_CF_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareClient:
    """Thin wrapper over Cloudflare DNS records API v4.

    Construct with explicit token + zone_id, or use :meth:`from_env`
    to read ``CLOUDFLARE_TOKEN`` and ``CLOUDFLARE_ZONE_ID``.
    """

    def __init__(self, token: str, zone_id: str):
        if not token:
            raise AuthError("CLOUDFLARE_TOKEN is empty")
        if not zone_id:
            raise ValidationError("CLOUDFLARE_ZONE_ID is empty")
        self.token = token
        self.zone_id = zone_id
        self.http = HttpClient(
            base_url=_CF_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15.0,
            verify_ssl=True,
        )

    @classmethod
    def from_env(cls) -> "CloudflareClient":
        token = os.environ.get("CLOUDFLARE_TOKEN", "").strip()
        zone_id = os.environ.get("CLOUDFLARE_ZONE_ID", "").strip()
        return cls(token, zone_id)

    # -----------------------------------------------------------------
    # Zone metadata
    # -----------------------------------------------------------------

    def get_zone(self) -> dict:
        """Return zone metadata (name, status, etc.)."""
        resp = self.http.request("GET", f"/zones/{self.zone_id}")
        return _unwrap(resp)

    # -----------------------------------------------------------------
    # DNS records — list / get / create / update / delete
    # -----------------------------------------------------------------

    def list_records(
        self,
        *,
        name: Optional[str] = None,
        type: Optional[str] = None,
        per_page: int = 100,
    ) -> list[dict]:
        """List records, optionally filtered server-side by name/type.

        Pagination handled transparently: walks `?page=N` until
        `result_info.total_pages` is exhausted. Returns concatenated
        list.
        """
        all_records: list[dict] = []
        page = 1
        while True:
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if name:
                params["name"] = name
            if type:
                params["type"] = type
            resp = self.http.request(
                "GET",
                f"/zones/{self.zone_id}/dns_records",
                params=params,
            )
            if not resp.get("success"):  # type: ignore[union-attr]
                raise UpstreamError(
                    f"cloudflare list_records failed: {resp.get('errors')}"
                )
            chunk = resp.get("result") or []
            all_records.extend(chunk)
            info = resp.get("result_info") or {}
            total_pages = info.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1
        return all_records

    def get_record(self, record_id: str) -> dict:
        resp = self.http.request(
            "GET",
            f"/zones/{self.zone_id}/dns_records/{record_id}",
        )
        return _unwrap(resp)

    def create_record(self, payload: dict) -> dict:
        resp = self.http.request(
            "POST",
            f"/zones/{self.zone_id}/dns_records",
            json=payload,
        )
        return _unwrap(resp)

    def update_record(self, record_id: str, payload: dict) -> dict:
        resp = self.http.request(
            "PATCH",
            f"/zones/{self.zone_id}/dns_records/{record_id}",
            json=payload,
        )
        return _unwrap(resp)

    def delete_record(self, record_id: str) -> dict:
        resp = self.http.request(
            "DELETE",
            f"/zones/{self.zone_id}/dns_records/{record_id}",
        )
        # Delete returns {"result": {"id": "..."}}
        return _unwrap(resp)


def _unwrap(resp: dict) -> dict:
    """Cloudflare API v4 wraps everything in ``{result, success, errors}``.

    Extract the ``result`` field if success=True, else raise UpstreamError.
    """
    if not isinstance(resp, dict):
        raise UpstreamError(f"cloudflare response not a dict: {type(resp).__name__}")
    if not resp.get("success", False):
        errors = resp.get("errors") or []
        msg = "; ".join(
            f"[{e.get('code')}] {e.get('message')}" for e in errors
        ) or "unknown error"
        raise UpstreamError(f"cloudflare API error: {msg}")
    return resp.get("result") or {}
