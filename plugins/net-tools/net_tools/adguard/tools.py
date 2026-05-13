"""AdGuard Home MCP tools — 6 tools en v0.1.0.

Patrón idéntico a cloudflare/tools.py:
- Pure functions con validación + business logic + envelope return.
- Mutating tools gated por ``NETTOOLS_ALLOW_MUTATIONS=true`` (filtrado
  en server.py al registrar).
- ``confirm=True`` obligatorio en mutating tools.

Multi-instance: cada tool toma ``host_ref`` y resuelve via env vars
(ADGUARD_<HOST_REF>_HOST/_USER/_PASSWORD).

Idempotency:
- ``set_rewrites`` (atomic bulk replace): naturalmente idempotente.
- ``add_rewrite`` / ``remove_rewrite``: helpers sobre set; idempotency
  documentada por tool.
"""
from __future__ import annotations

import re
from typing import Optional

from ..errors import NetToolsError, ValidationError, envelope_error
from ..models import Rewrite
from .client import AdGuardClient

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_IPV6_HINT_RE = re.compile(r"^[0-9a-fA-F:]+$")


def _validate_answer(answer: str) -> None:
    """Best-effort: IPv4, IPv6, o FQDN (CNAME target). Empty rejected."""
    if not answer or not answer.strip():
        raise ValidationError("answer is required (IPv4, IPv6 or FQDN target)")
    if _IPV4_RE.match(answer):
        return
    if ":" in answer and _IPV6_HINT_RE.match(answer):
        return
    if "." in answer:  # FQDN candidato
        return
    raise ValidationError(
        f"answer {answer!r} must be IPv4, IPv6 or a hostname with at least one '.'"
    )


def _validate_domain(domain: str) -> None:
    if not domain or not domain.strip():
        raise ValidationError("domain is required")
    if " " in domain or "\n" in domain or "\r" in domain:
        raise ValidationError(f"domain {domain!r} contains whitespace")
    if len(domain) > 253:
        raise ValidationError(f"domain too long ({len(domain)} > 253 chars)")


# ---------------------------------------------------------------------------
# Tool 1 — list_rewrites (read)
# ---------------------------------------------------------------------------

def adguard_list_rewrites(
    host_ref: str,
    domain_filter: Optional[str] = None,
) -> dict:
    """List DNS rewrites on one AdGuard instance.

    Args:
        host_ref: instance label (e.g. ``"l1"`` → ADGUARD_L1_*).
        domain_filter: case-insensitive substring filter on domain.

    Returns ``{ok, data: {host_ref, rewrites: [Rewrite], count}}``.
    """
    try:
        client = AdGuardClient.from_host_ref(host_ref)
        raw = client.list_rewrites()

        df = domain_filter.lower() if domain_filter else None
        out: list[Rewrite] = []
        for row in raw:
            domain = row.get("domain", "")
            if df and df not in domain.lower():
                continue
            out.append(Rewrite(domain=domain, answer=row.get("answer", "")))

        return {
            "ok": True,
            "data": {
                "host_ref": host_ref,
                "rewrites": [r.model_dump() for r in out],
                "count": len(out),
            },
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 2 — set_rewrites (mutating, ATOMIC bulk replace)
# ---------------------------------------------------------------------------

def adguard_set_rewrites(
    host_ref: str,
    rewrites: list[dict],
    confirm: bool = False,
    dry_run: bool = False,
    allow_empty: bool = False,
) -> dict:
    """Replace the ENTIRE rewrites list with the provided one (atomic).

    Reads current state, computes diff, applies only the necessary
    add/remove ops. AdGuard does not expose a bulk PUT, so the
    operation is "add new ones + delete old ones". Order of ops is
    add-first to minimize the window where a domain is unresolved.

    Args:
        host_ref: instance label.
        rewrites: list of dicts ``{"domain": str, "answer": str}``.
        confirm: must be ``True`` (defense in depth).
        dry_run: if True, compute diff but don't apply.
        allow_empty: if ``rewrites=[]`` and this is False, refuse. Empty
            list wipes ALL rewrites — dangerous, requires explicit opt-in.

    Returns ``{ok, data: {host_ref, diff: {added, removed, unchanged}, applied}}``.
    """
    try:
        if not confirm:
            raise ValidationError(
                "confirm=True required for mutation. Re-call with confirm=True."
            )
        if not isinstance(rewrites, list):
            raise ValidationError(
                f"rewrites must be a list, got {type(rewrites).__name__}"
            )
        if not rewrites and not allow_empty:
            raise ValidationError(
                "rewrites=[] would wipe ALL rewrites. Pass allow_empty=True "
                "explicitly if that is the intent."
            )

        # Validate each entry pre-flight
        target: set[tuple[str, str]] = set()
        for i, entry in enumerate(rewrites):
            if not isinstance(entry, dict):
                raise ValidationError(f"rewrites[{i}] must be dict, got {type(entry).__name__}")
            domain = entry.get("domain", "")
            answer = entry.get("answer", "")
            _validate_domain(domain)
            _validate_answer(answer)
            key = (domain, answer)
            if key in target:
                raise ValidationError(f"duplicate entry: domain={domain!r} answer={answer!r}")
            target.add(key)

        client = AdGuardClient.from_host_ref(host_ref)
        current_raw = client.list_rewrites()
        current: set[tuple[str, str]] = {
            (row.get("domain", ""), row.get("answer", ""))
            for row in current_raw
        }

        added = sorted(target - current)
        removed = sorted(current - target)
        unchanged = len(target & current)

        if not dry_run:
            # Apply removes first to free up domain+answer slots, then adds.
            # Order matters less here than in DNS records — AdGuard rewrites
            # are domain-scoped, no conflict between (d, a1) and (d, a2).
            for domain, answer in removed:
                client.remove_rewrite(domain, answer)
            for domain, answer in added:
                client.add_rewrite(domain, answer)

        return {
            "ok": True,
            "data": {
                "host_ref": host_ref,
                "diff": {
                    "added": [{"domain": d, "answer": a} for d, a in added],
                    "removed": [{"domain": d, "answer": a} for d, a in removed],
                    "unchanged": unchanged,
                },
                "applied": not dry_run,
            },
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 3 — add_rewrite (mutating, helper)
# ---------------------------------------------------------------------------

def adguard_add_rewrite(
    host_ref: str,
    domain: str,
    answer: str,
    upsert: bool = False,
    confirm: bool = False,
) -> dict:
    """Add one rewrite. Idempotent.

    Args:
        host_ref: instance label.
        domain, answer: the rewrite to add.
        upsert: if True, if there's an existing rewrite for the same
            domain with a DIFFERENT answer, replace it. If False (default)
            and a conflict exists, return ``idempotency`` error.
        confirm: must be True.

    Idempotency:
    - Existe con mismo (domain, answer) → action="already_correct"
    - Existe con (domain, otra_answer) sin upsert → IdempotencyError
    - Existe con (domain, otra_answer) con upsert=True → action="updated"
    - No existe → action="added"
    """
    try:
        if not confirm:
            raise ValidationError(
                "confirm=True required for mutation. Re-call with confirm=True."
            )
        _validate_domain(domain)
        _validate_answer(answer)

        client = AdGuardClient.from_host_ref(host_ref)
        current = client.list_rewrites()
        same_domain = [r for r in current if r.get("domain") == domain]

        # Exact match → idempotent already_correct
        if any(r.get("answer") == answer for r in same_domain):
            return {
                "ok": True,
                "data": {
                    "host_ref": host_ref,
                    "domain": domain,
                    "answer": answer,
                    "action": "already_correct",
                },
            }

        # Domain exists with different answer
        if same_domain:
            if not upsert:
                from ..errors import IdempotencyError
                raise IdempotencyError(
                    f"domain {domain!r} already has answer(s) "
                    f"{[r.get('answer') for r in same_domain]!r}. "
                    "Pass upsert=True to replace.",
                    {"existing_answers": [r.get("answer") for r in same_domain]},
                )
            # upsert: remove all conflicting, then add
            for r in same_domain:
                client.remove_rewrite(domain, r.get("answer", ""))
            client.add_rewrite(domain, answer)
            return {
                "ok": True,
                "data": {
                    "host_ref": host_ref,
                    "domain": domain,
                    "answer": answer,
                    "action": "updated",
                },
            }

        # New domain
        client.add_rewrite(domain, answer)
        return {
            "ok": True,
            "data": {
                "host_ref": host_ref,
                "domain": domain,
                "answer": answer,
                "action": "added",
            },
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 4 — remove_rewrite (mutating, idempotente)
# ---------------------------------------------------------------------------

def adguard_remove_rewrite(
    host_ref: str,
    domain: str,
    answer: Optional[str] = None,
    confirm: bool = False,
) -> dict:
    """Remove rewrite(s). Idempotent (no-op if absent).

    Args:
        host_ref: instance label.
        domain: domain to remove from rewrites.
        answer: optional. If None, removes ALL rewrites for ``domain``.
            If set, only removes the exact (domain, answer) pair.
        confirm: must be True.

    Returns shape: ``{ok, data: {removed: [{domain, answer}], action}}``.
    ``action`` ∈ {"deleted", "already_absent"}.
    """
    try:
        if not confirm:
            raise ValidationError(
                "confirm=True required for mutation. Re-call with confirm=True."
            )
        _validate_domain(domain)
        if answer is not None:
            _validate_answer(answer)

        client = AdGuardClient.from_host_ref(host_ref)
        current = client.list_rewrites()
        to_remove = [
            r for r in current
            if r.get("domain") == domain
            and (answer is None or r.get("answer") == answer)
        ]

        if not to_remove:
            return {
                "ok": True,
                "data": {
                    "host_ref": host_ref,
                    "domain": domain,
                    "removed": [],
                    "action": "already_absent",
                },
            }

        for r in to_remove:
            client.remove_rewrite(domain, r.get("answer", ""))

        return {
            "ok": True,
            "data": {
                "host_ref": host_ref,
                "domain": domain,
                "removed": [
                    {"domain": domain, "answer": r.get("answer", "")}
                    for r in to_remove
                ],
                "action": "deleted",
            },
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 5 — list_filtering_rules (read)
# ---------------------------------------------------------------------------

def adguard_list_filtering_rules(
    host_ref: str,
    enabled_only: bool = False,
    pattern_filter: Optional[str] = None,
) -> dict:
    """List user rules + filter lists from AdGuard.

    Returns both ``user_rules`` (free-form list of strings — each one
    a rule like ``||doubleclick.net^`` or ``@@||my-isp-tracker.com^``)
    and ``filter_lists`` (subscribed external lists with metadata).
    """
    try:
        client = AdGuardClient.from_host_ref(host_ref)
        status = client.get_filtering_status()
        user_rules: list[str] = status.get("user_rules", []) or []
        if pattern_filter:
            user_rules = [r for r in user_rules if pattern_filter in r]

        filters_raw = status.get("filters", []) or []
        filter_lists = []
        for f in filters_raw:
            if enabled_only and not f.get("enabled", False):
                continue
            filter_lists.append({
                "id": f.get("id"),
                "name": f.get("name", ""),
                "url": f.get("url", ""),
                "enabled": bool(f.get("enabled", False)),
                "rules_count": int(f.get("rules_count", 0)),
                "last_updated": f.get("last_updated"),
            })

        return {
            "ok": True,
            "data": {
                "host_ref": host_ref,
                "user_rules": user_rules,
                "user_rules_count": len(user_rules),
                "filter_lists": filter_lists,
                "filter_lists_count": len(filter_lists),
            },
        }
    except NetToolsError as exc:
        return envelope_error(exc)


# ---------------------------------------------------------------------------
# Tool 6 — query_log_search (read)
# ---------------------------------------------------------------------------

def adguard_query_log_search(
    host_ref: str,
    domain_filter: Optional[str] = None,
    response_status: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """Search recent DNS queries with optional filters.

    Args:
        host_ref: instance label.
        domain_filter: substring search; passed verbatim to AdGuard's
            ``search`` query param.
        response_status: AdGuard enum — "all", "filtered", "blocked",
            "blocked_safebrowsing", "blocked_parental", "whitelisted",
            "rewritten", "safe_search", "processed".
        limit: max entries to return, capped at 1000 server-side.

    Returns ``{ok, data: {host_ref, queries: [...], count, oldest}}``.
    """
    try:
        if limit < 1:
            raise ValidationError("limit must be >= 1")
        limit = min(limit, 1000)

        valid_status = {
            None, "all", "filtered", "blocked", "blocked_safebrowsing",
            "blocked_parental", "whitelisted", "rewritten", "safe_search",
            "processed",
        }
        if response_status not in valid_status:
            raise ValidationError(
                f"response_status {response_status!r} not in {sorted(s for s in valid_status if s)}"
            )

        client = AdGuardClient.from_host_ref(host_ref)
        resp = client.query_log(
            limit=limit,
            search=domain_filter,
            response_status=response_status,
        )

        # Normalize entries
        data_rows = resp.get("data", []) or []
        out_queries = []
        for row in data_rows:
            out_queries.append({
                "ts": row.get("time"),  # AdGuard returns "time" not "ts"
                "domain": (row.get("question") or {}).get("name", ""),
                "client": row.get("client", ""),
                "type": (row.get("question") or {}).get("type", ""),
                "status": row.get("reason", ""),
                "reply": _summarize_reply(row),
            })

        return {
            "ok": True,
            "data": {
                "host_ref": host_ref,
                "queries": out_queries,
                "count": len(out_queries),
                "oldest": resp.get("oldest"),
            },
        }
    except NetToolsError as exc:
        return envelope_error(exc)


def _summarize_reply(row: dict) -> Optional[str]:
    """Best-effort string summary of the answer (depends on record type)."""
    answer = row.get("answer")
    if not answer:
        return None
    if isinstance(answer, list) and answer:
        first = answer[0]
        if isinstance(first, dict):
            return first.get("value")
    return None
