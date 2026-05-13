"""Multi-instance helper for backends that operate against N hosts.

Pattern: a backend (adguard, future redis-cluster, etc.) has multiple
homologous instances (L1, L2, VPS...). The tool caller passes a
``host_ref`` like ``"l1"``, ``"vps"`` and the resolver locates the
host config in env vars:

  <BACKEND>_<INSTANCE_UPPER>_HOST       (required, full URL)
  <BACKEND>_<INSTANCE_UPPER>_USER       (optional, depends on backend)
  <BACKEND>_<INSTANCE_UPPER>_PASSWORD   (optional)
  <BACKEND>_<INSTANCE_UPPER>_TOKEN      (optional)

Example for AdGuard:
  ADGUARD_L1_HOST=http://10.0.1.14:3000
  ADGUARD_L1_USER=admin
  ADGUARD_L1_PASSWORD=...

This module is intentionally backend-agnostic — adguard/client.py
uses it via :func:`resolve_instance("ADGUARD", "l1")` and gets back
a dict with the fields it needs.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .errors import ValidationError

_INSTANCE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$", re.IGNORECASE)


def resolve_instance(backend: str, host_ref: str) -> dict[str, Optional[str]]:
    """Look up env vars for one instance of one backend.

    Args:
        backend: backend prefix WITHOUT trailing underscore (e.g. "ADGUARD",
            "PIHOLE" — the latter not used now but the resolver is generic).
        host_ref: instance label, alpha-numeric. Mapped to upper case for
            env var lookup.

    Returns dict with keys: ``host``, ``user``, ``password``, ``token``.
    Missing optional fields are None. ``host`` is REQUIRED — if missing,
    raises :class:`ValidationError` with a helpful message.

    Side-effect free, no network calls.
    """
    if not host_ref or not host_ref.strip():
        raise ValidationError("host_ref is required (e.g. 'l1', 'vps').")
    if not _INSTANCE_NAME_RE.match(host_ref):
        raise ValidationError(
            f"host_ref {host_ref!r} invalid — must match {_INSTANCE_NAME_RE.pattern} "
            "(alphanumeric + dash + underscore, ≤32 chars)."
        )

    prefix = f"{backend}_{host_ref.upper()}_"
    host = os.environ.get(f"{prefix}HOST", "").strip()
    if not host:
        raise ValidationError(
            f"{prefix}HOST not set. Persist via: "
            f"router_add_credential('{prefix}HOST', 'http://x.y.z.w:port')"
        )
    return {
        "host": host.rstrip("/"),
        "user": os.environ.get(f"{prefix}USER", "").strip() or None,
        "password": os.environ.get(f"{prefix}PASSWORD", "").strip() or None,
        "token": os.environ.get(f"{prefix}TOKEN", "").strip() or None,
    }


def list_known_instances(backend: str) -> list[str]:
    """Scan env for all instances of a backend.

    Returns lower-case instance labels. Useful for tools that want to
    enumerate ("list all my adguards") instead of forcing a specific one.

    Detection: looks for ``<BACKEND>_<X>_HOST`` keys and extracts ``X``.
    """
    prefix = f"{backend}_"
    suffix = "_HOST"
    out: list[str] = []
    for key in os.environ:
        if key.startswith(prefix) and key.endswith(suffix):
            middle = key[len(prefix):-len(suffix)]
            if _INSTANCE_NAME_RE.match(middle):
                out.append(middle.lower())
    return sorted(set(out))
