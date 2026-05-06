# Plan — Plugin gpon: GitHub Issues como fuente de consulta on-demand

**Estado**: borrador, no ejecutar hasta OK del operador.
**Fecha**: 2026-05-06 (revisado tras feedback del operador).
**Ámbito**: `plugins/gpon/` dentro de `mimir-mcp` (repo upstream
`CTRQuko/gpon-mcp`).

---

## Context

El plugin gpon hoy arrastra el sitio Jekyll completo de
`hack-gpon/hack-gpon.github.io` clonado en `knowledge/hack-gpon/`. El
operador quiere:

1. **Eliminar el clon Jekyll completo** — pesa MB, se desactualiza, y
   no aporta más que `data/models.json` ya local.
2. **Añadir consulta on-demand a los Issues del repo** — porque ahí
   aparecen *fixes* y *workarounds* de la comunidad. Caso de uso real:
   "fix de traslación de VLAN para sticks Huawei" → debería poder
   buscarse desde mimir y obtener el resultado al momento.

> **Cambio de diseño respecto al borrador anterior**: NO se monta caché
> local de issues. NO sync periódico. NO state file. La tool consulta
> la GitHub Search API en cada invocación. Resultados frescos, sin
> mantenimiento, sin estado que se pueda corromper.

---

## Verificación previa con caso real

Probado contra GitHub API hoy (2026-05-06) con queries naturales:

| Query | Total | Top result |
|-------|-------|-----------|
| `vlan huawei` | 2 | #328 [closed] "Go back to image 0" — fix `fw_setenv image0_is_valid 1` |
| `vlan translation` | 1 | #251 [closed] "ONT FS - VLAN Tagging" |
| `huawei vlan` | 2 | #323 [open] + #328 |
| `tag rewrite` | 1 | #423 [open] |

Confirma: **queries naturales funcionan**. El operador formula la duda
en lenguaje libre (`"fix vlan translation huawei"`), la API hace fuzzy
match en title + body de issues + PRs, y devuelve resultados ordenados
por relevancia. Para cada hit, una segunda llamada (`get_issue`)
devuelve el body completo + comentarios.

---

## Estado actual

### Fuentes en el plugin

| Fuente | Path | Acción |
|--------|------|--------|
| `data/models.json` | `plugins/gpon/gpon_mcp/data/models.json` | **Se queda** — snapshot 54 modelos, útil sin internet |
| Clon Jekyll completo | `plugins/gpon/gpon_mcp/knowledge/hack-gpon/` | **Se elimina** entero (carpeta, `.git`, `_ont/`, `_gpon/`, `_isp/`, etc.) |
| `knowledge_sync.py` (git clone/pull del Jekyll) | `plugins/gpon/gpon_mcp/knowledge_sync.py` | **Se elimina** entero |
| `knowledge_sync_v3.py` (raw GitHub fetch + cache) | `plugins/gpon/gpon_mcp/knowledge_sync_v3.py` | **Se simplifica** — solo el raw fetch puntual de detalles de modelo si hace falta. Puede que ni eso, depende de qué tools usen `models.json` directo |

### Tools MCP que cambian

| Tool actual | Acción |
|-------------|--------|
| `gpon_sync_knowledge_base(force)` | **Eliminar** — ya no hay clon que sincronizar |
| `gpon_get_module_docs(model_slug)` | **Eliminar** — depende de `_ont/` Jekyll. Si el operador la usa, sustituir por búsqueda en issues |
| `gpon_list_known_operators()` | **Decidir** — hoy lee `_isp/` Jekyll. Opciones: snapshot a `operators.json` (manual) o eliminar |
| `gpon_search_models(query)` | **Mantener** — usa `data/models.json` local |
| `gpon_get_model_details(slug)` | **Mantener** — usa `data/models.json` + raw fetch puntual |
| `gpon_list_known_modules()` | **Mantener** — usa `data/models.json` |

### Tools MCP que se añaden (consulta on-demand)

Solo dos, sin caché ni estado local:

```
gpon_search_issues(query: str, *, state="all", limit=10) -> list[dict]
gpon_get_issue(number: int, *, with_comments=True) -> dict
```

#### `gpon_search_issues(query, state, limit)`

Llama directo a GitHub Search API:

```
GET https://api.github.com/search/issues
    ?q=repo:hack-gpon/hack-gpon.github.io+{query}+state:{state}
    &per_page={limit}
```

Devuelve lista compacta:

```json
[
  {
    "number": 328,
    "state": "closed",
    "title": "Go back to image 0",
    "labels": [],
    "comments": 2,
    "url": "https://github.com/hack-gpon/hack-gpon.github.io/issues/328",
    "snippet": "Hi. I've tried booting Huawei modified image 5-1..."
  },
  ...
]
```

`snippet` es `body[:300]` para que el LLM/operador decida cuál leer en
detalle.

#### `gpon_get_issue(number, with_comments=True)`

Llama dos endpoints:

```
GET /repos/hack-gpon/hack-gpon.github.io/issues/{number}
GET /repos/hack-gpon/hack-gpon.github.io/issues/{number}/comments
```

Devuelve:

```json
{
  "number": 328,
  "state": "closed",
  "title": "Go back to image 0",
  "labels": [],
  "url": "...",
  "body": "<full issue body>",
  "comments": [
    {"user": "benbgg", "body": "Figured it! Should be: fw_setenv image0_is_valid 1"}
  ]
}
```

---

## Implementación

### Nuevo módulo `plugins/gpon/gpon_mcp/issues.py`

~80 LOC. Stdlib only (`urllib.request`, `urllib.parse`, `json`).

```python
"""GitHub Issues query for hack-gpon repo. On-demand only — no cache."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

REPO = "hack-gpon/hack-gpon.github.io"
API = "https://api.github.com"


def _request(path: str, *, params: dict | None = None) -> Any:
    url = f"{API}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GPON_GITHUB_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"GitHub API HTTP {e.code} on {path}: {e.read().decode('utf-8', 'replace')[:200]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub API unreachable on {path}: {e}") from e


def search_issues(query: str, *, state: str = "all", limit: int = 10) -> list[dict]:
    """Search issues + PRs in hack-gpon repo. Returns compact list."""
    if state not in ("open", "closed", "all"):
        raise ValueError(f"state must be open|closed|all, got {state!r}")
    q = f"repo:{REPO} {query}"
    if state != "all":
        q += f" state:{state}"
    data = _request(
        "/search/issues",
        params={"q": q, "per_page": min(max(1, limit), 30)},
    )
    return [
        {
            "number": item["number"],
            "state": item["state"],
            "title": item["title"],
            "labels": [l["name"] for l in item.get("labels", [])],
            "comments": item.get("comments", 0),
            "url": item["html_url"],
            "snippet": (item.get("body") or "").replace("\r\n", "\n")[:300],
        }
        for item in data.get("items", [])
    ]


def get_issue(number: int, *, with_comments: bool = True) -> dict:
    """Fetch a single issue with full body + (optionally) comments."""
    issue = _request(f"/repos/{REPO}/issues/{number}")
    out = {
        "number": issue["number"],
        "state": issue["state"],
        "title": issue["title"],
        "labels": [l["name"] for l in issue.get("labels", [])],
        "url": issue["html_url"],
        "body": issue.get("body") or "",
        "comments": [],
    }
    if with_comments and issue.get("comments", 0) > 0:
        comments = _request(f"/repos/{REPO}/issues/{number}/comments")
        out["comments"] = [
            {"user": c["user"]["login"], "body": c.get("body") or ""}
            for c in comments
        ]
    return out
```

### Wire en `gpon_mcp/server.py`

Añadir 2 tools MCP:

```python
@mcp.tool
def gpon_search_issues(query: str, state: str = "all", limit: int = 10) -> list[dict]:
    """Busca issues + PRs en hack-gpon/hack-gpon.github.io.

    Útil para encontrar fixes y workarounds de la comunidad. Devuelve
    lista compacta — usar gpon_get_issue para el detalle de uno.
    """
    return search_issues(query, state=state, limit=limit)


@mcp.tool
def gpon_get_issue(number: int, with_comments: bool = True) -> dict:
    """Devuelve un issue completo con body + comentarios (si los tiene)."""
    return get_issue(number, with_comments=with_comments)
```

Eliminar `gpon_sync_knowledge_base` y `gpon_get_module_docs`.
Decidir `gpon_list_known_operators` según Q1 abajo.

### Tests `plugins/gpon/tests/test_issues.py`

~10 tests con mocks de `urllib.request.urlopen`:
- `search_issues` con resultados → lista bien formada
- `search_issues` con `state` válido / inválido
- `search_issues` con limit clamp 1..30
- `get_issue` sin comments
- `get_issue` con comments
- HTTP error 4xx/5xx → RuntimeError legible
- URLError (red caída) → RuntimeError
- Token presente → header Authorization
- Token ausente → sin header
- Query con caracteres especiales escapados correctamente

### Manifest del plugin

`plugins/gpon/plugin.toml`:

- Bump version: 2.1.0 → 2.2.0.
- Añadir credential opcional `GPON_GITHUB_TOKEN` en
  `[security].credential_refs` (sin token funciona pero rate limit
  10 search/min anónimo vs 30/min con token).
- Eliminar referencias a `knowledge_sync_*` si están en el manifest.

### Eliminar Jekyll clone + módulo sync

```
rm -rf plugins/gpon/gpon_mcp/knowledge/hack-gpon/
rm plugins/gpon/gpon_mcp/knowledge_sync.py
```

`knowledge_sync_v3.py`: ver si es necesario tras eliminar las tools que
dependen de él. Probablemente sí elimina entero — solo se queda
`data/models.json` cargado directamente.

### `.gitignore`

Añadir `plugins/gpon/gpon_mcp/knowledge/` para que el clon viejo no
vuelva si alguien hace pull de upstream.

---

## Migration steps (ejecución cuando OK del operador)

```
Step 1: Inventario read-only — pytest plugins/gpon/ -v y registrar
        qué tests dependen del Jekyll clonado. (~10 min)

Step 2: Crear plugins/gpon/gpon_mcp/issues.py + tests test_issues.py
        con mocks. Solo el módulo nuevo, no toca el resto. (~45 min)

Step 3: Wire 2 tools MCP en server.py (gpon_search_issues,
        gpon_get_issue). Eliminar gpon_sync_knowledge_base y
        gpon_get_module_docs. Resolver gpon_list_known_operators
        según Q1. (~30 min)

Step 4: Eliminar carpeta knowledge/hack-gpon/, knowledge_sync.py,
        simplificar/eliminar knowledge_sync_v3.py. Adaptar tests
        existentes que se rompan. (~30 min)

Step 5: Bump version + plugin.toml + CHANGELOG. (~10 min)

Step 6: Suite verde + smoke test end-to-end:
        - gpon_search_issues("vlan huawei") → resultados
        - gpon_get_issue(328) → body + comments del issue (~10 min)

Step 7: Commit en mimir-mcp local + push (autorizado).
        Para repo upstream CTRQuko/gpon-mcp: requiere OK explícito
        (Q5).
```

Total estimado: ~2h efectivas.

---

## Open questions (decisiones del operador)

1. **`gpon_list_known_operators` (`_isp/`)**: ¿se elimina, o se hace un
   snapshot manual a `data/operators.json` para mantenerla? Si la usas
   raramente o nunca, recomiendo eliminarla.

2. **Token GitHub**: ¿activamos el credential opcional `GPON_GITHUB_TOKEN`?
   Sin token: 10 búsquedas/min (suficiente para uso humano, fallaría en
   loops). Con token (puedes usar el de `secrets/github-token.md`):
   30/min y mejor cuota agregada. Recomiendo: **soportar pero no exigir**
   — sin token funciona, con token va mejor.

3. **`gpon_get_module_docs`**: ¿la usabas? Si sí, sustituyo por
   `gpon_search_issues + gpon_get_issue` o por añadir más detalle a
   `gpon_get_model_details`?

4. **Repo upstream `CTRQuko/gpon-mcp`**: ¿quieres que el cambio se
   haga directamente en el upstream (push commits a tu fork) o trabajar
   primero en el local de mimir-mcp y luego replicar? **Tocar repo
   externo siempre requiere OK explícito** según core-security.md.

5. **Bonus**: ¿quieres también una tool `gpon_search_pulls` para PRs
   merged como fixes oficiales? (los issues incluyen PRs en GitHub
   Search, pero filtrar `is:pr is:merged` puede ser útil)

---

## Riesgos / mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Rate limit GitHub API anónimo (10 search/min) | Token opcional eleva a 30/min. Operación humana cabe holgadamente. |
| Issue eliminado o repo movido | Error legible "GitHub API HTTP 404" sin tirar el plugin. Tool retorna error, operador investiga. |
| Eliminar `gpon_get_module_docs` rompe flujo | Q3: confirmar uso real antes. Si se usa, reemplazar antes de eliminar. |
| Tests existentes que dependen del Jekyll clonado | Step 1 inventario. Adaptarlos en Step 4 con mocks o eliminarlos si la lógica desaparece. |
| Repo upstream desincronizado | Trabajar primero en local. Step 7 con OK explícito antes de tocar upstream. |

---

## Demo concreta del flujo (post-implementación)

Caso de uso del operador (`fix VLAN translation Huawei sticks`):

```
> gpon_search_issues(query="vlan huawei")
[
  {"number": 323, "state": "open", "title": "FS.com GPON-ONU-34-20BI incorrect tagging", ...},
  {"number": 328, "state": "closed", "title": "Go back to image 0", "snippet": "Hi. I've tried booting Huawei modified image 5-1..."},
]

> gpon_get_issue(number=328)
{
  "number": 328,
  "state": "closed",
  "title": "Go back to image 0",
  "body": "Hi. I've tried booting Huawei modified image 5-1. It does not work...",
  "comments": [
    {"user": "benbgg", "body": "Figured it! Should be: fw_setenv image0_is_valid 1"}
  ]
}
```

LLM interpreta: el fix está en el comentario — `fw_setenv image0_is_valid 1`.

> Probado real hoy con esa misma query — los resultados arriba son
> literales de la API. Funciona.
