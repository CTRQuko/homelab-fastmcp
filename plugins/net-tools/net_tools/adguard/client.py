"""AdGuard Home REST API client.

AdGuard exposes a REST API that uses Basic Auth (no token rotation).
We build one client per (instance, tool-call); short-lived.

Reference:
https://github.com/AdguardTeam/AdGuardHome/blob/master/openapi/openapi.yaml
"""
from __future__ import annotations

import base64
from typing import Any, Optional

from ..errors import AuthError, ValidationError
from ..http_client import HttpClient
from ..multi_instance import resolve_instance


class AdGuardClient:
    """Thin AdGuard Home REST wrapper for one instance.

    Construct with explicit fields or use :meth:`from_host_ref` to
    resolve a multi-instance label (e.g. "l1") via env vars.
    """

    def __init__(self, host: str, user: str, password: str, *, verify_ssl: bool = True):
        if not host:
            raise ValidationError("host is required")
        if not user or not password:
            raise AuthError("AdGuard requires user + password (Basic Auth)")
        self.host = host.rstrip("/")
        self.user = user
        # Basic Auth header (not stored as plaintext password attribute)
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.http = HttpClient(
            base_url=self.host,
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15.0,
            verify_ssl=verify_ssl,
        )

    @classmethod
    def from_host_ref(cls, host_ref: str) -> "AdGuardClient":
        """Resolve via ADGUARD_<HOST_REF>_HOST / _USER / _PASSWORD env vars."""
        info = resolve_instance("ADGUARD", host_ref)
        if not info["user"] or not info["password"]:
            raise AuthError(
                f"ADGUARD_{host_ref.upper()}_USER / _PASSWORD must be set "
                f"in vault (Basic Auth)."
            )
        return cls(
            host=info["host"],  # type: ignore[arg-type]
            user=info["user"],
            password=info["password"],
        )

    # -----------------------------------------------------------------
    # Rewrites (DNS rewrite rules — main use case)
    # -----------------------------------------------------------------

    def list_rewrites(self) -> list[dict]:
        """GET /control/rewrite/list — returns list of {domain, answer}."""
        resp = self.http.request("GET", "/control/rewrite/list")
        if not isinstance(resp, list):
            raise ValidationError(
                f"unexpected rewrites response shape: {type(resp).__name__}"
            )
        return resp

    def add_rewrite(self, domain: str, answer: str) -> None:
        """POST /control/rewrite/add — single entry. NO replace semantics."""
        self.http.request(
            "POST",
            "/control/rewrite/add",
            json={"domain": domain, "answer": answer},
        )

    def remove_rewrite(self, domain: str, answer: str) -> None:
        """POST /control/rewrite/delete — body must be the exact entry."""
        self.http.request(
            "POST",
            "/control/rewrite/delete",
            json={"domain": domain, "answer": answer},
        )

    # -----------------------------------------------------------------
    # Filtering rules (user-rules + filter lists)
    # -----------------------------------------------------------------

    def get_filtering_status(self) -> dict:
        """GET /control/filtering/status — user_rules + filters list."""
        resp = self.http.request("GET", "/control/filtering/status")
        if not isinstance(resp, dict):
            raise ValidationError(
                f"unexpected filtering status shape: {type(resp).__name__}"
            )
        return resp

    # -----------------------------------------------------------------
    # Query log search
    # -----------------------------------------------------------------

    def query_log(
        self,
        *,
        older_than: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        search: Optional[str] = None,
        response_status: Optional[str] = None,
    ) -> dict:
        """GET /control/querylog with optional filters.

        AdGuard returns {data: [...], oldest: "...", ...}.
        ``response_status`` accepts AdGuard's enum: "all", "filtered",
        "blocked", "blocked_safebrowsing", "blocked_parental", "whitelisted",
        "rewritten", "safe_search", "processed".
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if older_than:
            params["older_than"] = older_than
        if search:
            params["search"] = search
        if response_status:
            params["response_status"] = response_status
        resp = self.http.request("GET", "/control/querylog", params=params)
        if not isinstance(resp, dict):
            raise ValidationError(
                f"unexpected querylog shape: {type(resp).__name__}"
            )
        return resp
