"""Error hierarchy shared across submodules.

Tools catch ``NetToolsError`` subclasses and translate to the
``{ok: False, error: ..., error_type: ...}`` envelope returned to the
MCP client. Untyped exceptions bubble to mimir's audit log.
"""
from __future__ import annotations


class NetToolsError(Exception):
    """Base — do not instantiate directly. Use a subclass."""


class AuthError(NetToolsError):
    """401 / 403 / SID inválido / token expirado."""


class NotFoundError(NetToolsError):
    """Resource no existe (record_id, instance, etc.)."""


class ValidationError(NetToolsError):
    """Pre-flight reject (zone wrong, proxied=true en zona privada, etc.).

    Raised BEFORE any network call when input is structurally invalid
    or violates a hard constraint (e.g. forced ``proxied=False`` in
    homelab zones).
    """


class UpstreamError(NetToolsError):
    """5xx del backend, timeout, response malformado."""


class IdempotencyError(NetToolsError):
    """Resource ya existe con valor distinto al pedido.

    Caller decide si reintentar con ``upsert=True`` o llamar la tool
    ``update_*`` correspondiente. The error carries the existing
    resource's identifier in ``args[1]`` (when available).
    """


def envelope_error(exc: NetToolsError) -> dict:
    """Convert any NetToolsError into the standard MCP return envelope."""
    mapping = {
        AuthError: "auth",
        NotFoundError: "not_found",
        ValidationError: "validation",
        UpstreamError: "upstream",
        IdempotencyError: "idempotency",
    }
    error_type = mapping.get(type(exc), "unknown")
    out: dict = {
        "ok": False,
        "error": str(exc),
        "error_type": error_type,
    }
    # Surface any extra context the exception carries.
    if len(exc.args) > 1 and isinstance(exc.args[-1], dict):
        out["context"] = exc.args[-1]
    return out
