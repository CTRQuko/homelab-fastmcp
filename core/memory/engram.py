"""Engram memory backend (HTTP API).

Delegates persistence to a running ``engram serve`` instance over HTTP.
Engram is the persistent memory tool for AI coding agents
(https://github.com/Gentleman-Programming/engram), with SQLite + FTS5
storage and an HTTP API on port 7437 by default.

Configuration via ``router.toml``::

    [memory]
    backend = "engram"
    base_url = "http://127.0.0.1:7437"   # optional, default shown
    project = "homelab"                   # optional — scopes saves
    session_id = "mimir-router"           # optional, default shown
    timeout = 5                            # optional seconds, default 5

Requires ``engram serve`` running. If unreachable at boot, the backend
raises ``RuntimeError`` so :func:`load_backend` fails loud and Mimir
reports the memory layer as ``degraded``. Operators who do not run
engram should pick ``backend = "noop"`` or ``"sqlite"`` instead.

Design notes:

* Stdlib only (urllib.request) — no extra dependency added.
* Synchronous — every method blocks until engram replies. Meant for
  occasional plugin calls, not hot paths.
* Endpoint surface used: ``GET /health``, ``GET /search``, ``GET
  /observations/<id>``, ``POST /observations``, ``PATCH
  /observations/<id>``, ``DELETE /observations/<id>``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import MemoryBackend


log = logging.getLogger(__name__)


class EngramMemory(MemoryBackend):
    """Memory backend that delegates to a local engram HTTP API."""

    name = "engram"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7437",
        project: str | None = None,
        session_id: str = "mimir-router",
        timeout: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._project = project
        self._session_id = session_id
        self._timeout = timeout

        # Fail-loud al boot: si engram serve no responde, no instanciamos.
        # El operador ve el error en router_status (degraded) y arregla
        # antes de intentar usar memoria.
        try:
            self._http_get("/health")
        except Exception as e:
            raise RuntimeError(
                f"engram HTTP API no alcanzable en {self._base_url}: {e}. "
                "¿Está 'engram serve' corriendo? Si no quieres usar engram, "
                "pon backend = 'noop' o 'sqlite' en router.toml."
            ) from e

        # Asegurar que la session_id existe en engram (FK constraint).
        # Engram requiere `project` para crear sessions; si no está set, lo
        # skipeamos — el primer save() fallará con FK error claro y el
        # operador sabrá que tiene que añadir project al config.
        if self._project:
            self._ensure_session()
        else:
            log.warning(
                "EngramMemory: 'project' no configurado — session %r no se "
                "preparó. Si el primer save() falla con FK error, añade "
                "project al config de [memory] en router.toml.",
                self._session_id,
            )

    def _ensure_session(self) -> None:
        """Crea la session_id en engram si no existe (idempotente).

        engram trata POST /sessions como upsert (devuelve 201 incluso si la
        session ya existe). Errores 400 explícitos de "already exists" se
        toleran como red de seguridad por si el comportamiento cambia.
        """
        body = {"id": self._session_id, "project": self._project}
        try:
            self._http_request("POST", "/sessions", body)
        except RuntimeError as e:
            msg = str(e)
            if "HTTP 400" in msg and (
                "already exists" in msg.lower()
                or "unique constraint" in msg.lower()
                or "duplicate" in msg.lower()
            ):
                log.debug(
                    "engram session %r already exists, reusing",
                    self._session_id,
                )
                return
            raise

    # ----- HTTP helpers -------------------------------------------------

    def _http_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise RuntimeError(
                f"engram {method} {path} → HTTP {e.code}: {err_body or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"engram {method} {path} unreachable: {e.reason}"
            ) from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # /health devuelve cuerpo no-JSON, otros endpoints sí. Si llega aquí
            # con un endpoint que esperaba JSON, propagamos texto.
            return raw

    def _http_get(self, path: str) -> Any:
        return self._http_request("GET", path)

    # ----- MemoryBackend interface --------------------------------------

    def save(self, content: str, tags: list[str] | None = None, **kw: Any) -> str:
        """Crear nueva observación. Devuelve el ID como string.

        kwargs reconocidos:
        - title (default: primeros 60 chars de content)
        - type (default: "note")
        - project (default: el del config si está set)
        - scope (default: "project")
        - topic_key (opcional)
        """
        body = {
            "session_id": self._session_id,
            "title": kw.get("title") or content[:60].strip() or "(untitled)",
            "content": content,
            "type": kw.get("type", "note"),
            "scope": kw.get("scope", "project"),
        }
        project = kw.get("project") or self._project
        if project:
            body["project"] = project
        topic_key = kw.get("topic_key")
        if topic_key:
            body["topic_key"] = topic_key
        if tags:
            # engram no tiene un campo "tags" nativo; convertimos a hint en
            # title o lo guardamos en metadata si engram lo soporta.
            # Por ahora ignoramos silenciosamente — los tags no son parte
            # del schema de engram.
            log.debug("engram.save: ignoring tags=%r (no native field)", tags)

        result = self._http_request("POST", "/observations", body)
        if not isinstance(result, dict) or "id" not in result:
            raise RuntimeError(
                f"engram save devolvió respuesta inesperada: {result!r}"
            )
        return str(result["id"])

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        params = {"q": query, "limit": str(limit)}
        if self._project:
            params["project"] = self._project
        qs = urllib.parse.urlencode(params)
        result = self._http_get(f"/search?{qs}")
        if not isinstance(result, list):
            raise RuntimeError(
                f"engram search devolvió respuesta inesperada: {type(result).__name__}"
            )
        return result

    def get(self, id: str) -> dict[str, Any]:
        result = self._http_get(f"/observations/{id}")
        if not isinstance(result, dict):
            raise RuntimeError(
                f"engram get devolvió respuesta inesperada: {type(result).__name__}"
            )
        return result

    def update(self, id: str, content: str) -> None:
        self._http_request(
            "PATCH",
            f"/observations/{id}",
            {"content": content},
        )

    def delete(self, id: str) -> None:
        self._http_request("DELETE", f"/observations/{id}")
