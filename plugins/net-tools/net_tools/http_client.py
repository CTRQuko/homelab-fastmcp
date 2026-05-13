"""httpx wrapper with retries, secret masking, and uniform error mapping.

Submodule clients (cloudflare, pihole, adguard) layer auth on top of
this base.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .errors import AuthError, NotFoundError, UpstreamError, ValidationError

log = logging.getLogger(__name__)

# Headers whose values must never appear in logs verbatim.
_SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key", "x-auth-key"}


def mask_token(value: str) -> str:
    """Return a masked form for log output: first 5 + last 3 chars."""
    if not value or len(value) < 12:
        return "***"
    return f"{value[:5]}***{value[-3:]}"


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip sensitive header values from a dict copy (for log only)."""
    out = {}
    for k, v in headers.items():
        out[k] = "***REDACTED***" if k.lower() in _SENSITIVE_HEADERS else v
    return out


# Status codes that we retry on (transient).
_RETRY_STATUSES = {502, 503, 504, 429}


class HttpClient:
    """Single-host httpx client with retries and uniform error mapping.

    Submodules instantiate one client per backend host (CF API,
    Pi-hole instance, AdGuard instance). The client is short-lived
    (per-tool-call). For long-lived state (Pi-hole SID), submodules
    keep their own object alongside the http client.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 15.0,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        extra_headers: Optional[dict[str, str]] = None,
        retries: int = 2,
    ) -> dict | list:
        """Execute one HTTP request with retry on transient failures.

        Raises ``AuthError``, ``NotFoundError``, ``ValidationError``,
        or ``UpstreamError`` depending on status. Returns parsed JSON
        (dict or list) on success.
        """
        url = f"{self.base_url}{path}"
        headers = {**self.headers, **(extra_headers or {})}

        log.debug(
            "HTTP %s %s headers=%s", method, url, _mask_headers(headers)
        )

        attempt = 0
        backoff = 0.1  # 100ms initial
        while True:
            attempt += 1
            try:
                with httpx.Client(
                    timeout=self.timeout, verify=self.verify_ssl
                ) as client:
                    resp = client.request(
                        method, url, headers=headers, json=json, params=params
                    )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt > retries:
                    raise UpstreamError(
                        f"network failure after {attempt} attempts: {exc}"
                    ) from exc
                log.warning("transient http error (attempt %d): %s", attempt, exc)
                _sleep(backoff)
                backoff = min(backoff * 5, 2.0)
                continue

            # Map status codes to typed errors.
            if resp.status_code in {401, 403}:
                raise AuthError(
                    f"{resp.status_code} {resp.reason_phrase} on {method} {path}"
                )
            if resp.status_code == 404:
                raise NotFoundError(
                    f"404 on {method} {path}: {resp.text[:200]}"
                )
            if 400 <= resp.status_code < 500:
                # Other 4xx: not retryable, surface as validation.
                raise ValidationError(
                    f"{resp.status_code} {resp.reason_phrase}: {resp.text[:500]}"
                )
            if resp.status_code in _RETRY_STATUSES and attempt <= retries:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after else backoff
                log.warning(
                    "retryable %d on attempt %d, sleeping %.1fs",
                    resp.status_code, attempt, wait,
                )
                _sleep(wait)
                backoff = min(backoff * 5, 2.0)
                continue
            if not (200 <= resp.status_code < 300):
                raise UpstreamError(
                    f"{resp.status_code} {resp.reason_phrase}: {resp.text[:500]}"
                )

            try:
                return resp.json()
            except ValueError as exc:
                raise UpstreamError(
                    f"non-JSON response from {method} {path}: {resp.text[:200]}"
                ) from exc


def _sleep(seconds: float) -> None:
    """Indirection so tests can monkey-patch this without touching time."""
    import time

    time.sleep(seconds)
