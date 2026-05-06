# Plan — Plugin gpon: GitHub Issues como fuente de consulta on-demand

**Estado**: v3 — cerrado tras feedback. Pendiente OK final del operador para ejecutar.
**Fecha**: 2026-05-06.

> Cambios v3 vs v2:
> - `gpon_search_operator_issues` → renombrado a `gpon_search_stick_issues`
>   (las issues están atadas a configs de stick, no a operadores).
> - Nueva tool `gpon_get_module_wiki(slug)` (raw markdown).
> - Nueva tool `gpon_get_module_full(slug)` que **combina**
>   `gpon_get_model_details + gpon_get_module_wiki + gpon_search_stick_issues`
>   — esa es la "vista completa para crear entrada de stick".
> - `gpon_list_known_operators` mantiene; `data/operators.json` snapshot
>   inicial + estrategia de scraping web diferida a fase 2.
> - Pre-step: helper compartido `core/secrets.get_github_token()`.
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
| `gpon_get_module_docs(model_slug)` | **Eliminar** — sustituido por `gpon_get_module_full(slug)` que integra wiki + details + issues |
| `gpon_list_known_operators()` | **Mantener** — fuente cambia: hoy lee `_isp/` Jekyll → tras el cambio lee `data/operators.json` (ver sección "Operadores" abajo) |
| `gpon_search_models(query)` | **Mantener** — usa `data/models.json` local |
| `gpon_get_model_details(slug)` | **Mantener** — usa `data/models.json` + raw fetch puntual |
| `gpon_list_known_modules()` | **Mantener** — usa `data/models.json` |

### Tools MCP que se añaden (consulta on-demand)

Sin caché ni estado local:

```
gpon_search_issues(query: str, *, state="all", limit=10) -> list[dict]
gpon_get_issue(number: int, *, with_comments=True) -> dict
gpon_search_stick_issues(slug: str, *, only_with_fix=False, limit=10) -> list[dict]
gpon_get_module_wiki(slug: str) -> str
gpon_get_module_full(slug: str) -> dict   # combina las 3 fuentes
```

**Las issues están atadas a configs de stick**, no a operadores. La
relación con operadores es transitiva (issue del stick X usado con
operador Y), por eso la búsqueda primaria es por slug de stick.

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

#### `gpon_search_stick_issues(slug, only_with_fix=False, limit=10)`

Búsqueda especializada por modelo de stick. El slug es el del catálogo
(`huawei-hg8120c`, `nokia-7368-isam`, etc.). Internamente:

```python
# slug → terms a buscar (parts del slug + alias del catálogo)
terms = _slug_to_search_terms(slug, models_json)
# e.g. "huawei-hg8120c" → ["huawei hg8120c", "HG8120c", "HG8120"]
query = " OR ".join(terms)
results = search_issues(query, state="all", limit=limit*2)
if only_with_fix:
    results = [r for r in results if _looks_like_fix(r)]
return results[:limit]
```

`_looks_like_fix` heurística: issue tiene label `fix`/`solved`, está
`closed`, o el snippet contiene patrones tipo `fw_setenv`, `Solution:`,
`Fixed`, `Solved`. No es perfecto pero filtra ruido.

#### `gpon_get_module_wiki(slug)`

Fetch directo del markdown crudo del Jekyll, sin clonar:

```
GET https://raw.githubusercontent.com/hack-gpon/hack-gpon.github.io/main/_ont/{slug}.md
```

Devuelve el string del markdown. Si 404, retorna error legible.

#### `gpon_get_module_full(slug)` — vista completa

La pieza clave para "crear entrada de stick con info completa":

```python
def gpon_get_module_full(slug: str) -> dict:
    return {
        "model": gpon_get_model_details(slug),     # estructurado de models.json
        "wiki": gpon_get_module_wiki(slug),        # markdown crudo del repo
        "issues": gpon_search_stick_issues(slug, limit=5),  # top 5 issues
    }
```

Una sola llamada → toda la info disponible del stick. El LLM lee y crea
una entrada estructurada. Si una de las 3 fuentes falla (red caída,
slug desconocido en wiki), las otras dos siguen llegando — el dict
incluye un campo `errors` con lo que no se pudo obtener.

---

---

## Operadores — fuente de datos (en dos fases)

### Fase 1 (incluida en este cambio): `data/operators.json`

Snapshot manual extraído del `_isp/` Jekyll actual ANTES de borrar el
clon. Estructura propuesta:

```json
{
  "movistar": {
    "name": "Movistar (Telefónica España)",
    "country": "ES",
    "vlan": 6,
    "loid_format": "<formato esperado del Logical ONU ID>",
    "ploam_password": "...",
    "tags": ["dual-stack", "PPPoE"],
    "notes": "GPON SN must end in...",
    "tested_sticks": ["huawei-hg8120c", "nokia-7368-isam"]
  },
  "vodafone-es": { ... },
  "orange-es": { ... },
  "yoigo": { ... },
  "digi-es": { ... },
  "pepephone": { ... }
}
```

Mantenido a mano. Cuando el operador detecte cambio (cambio de VLAN
upstream, nueva ISP), edita el JSON y commitea. Es el patrón que ya
funciona con `data/models.json`.

`gpon_list_known_operators()` lee este JSON.
Nuevo: `gpon_get_operator_details(slug)` devuelve un operador concreto.

### Fase 2 (DIFERIDA — separada del PR de issues)

Tool nueva `gpon_search_operator_web(operator_name)` que scrapea fuentes
web públicas para info actualizada de configs. Diseño preliminar (no
incluido en este plan, requiere su propio análisis):

| Fuente | Cobertura | Robustez del scraper |
|--------|-----------|----------------------|
| `bandaancha.eu/foros` | España fuerte | Alta — foros estables, structure conocida |
| `adslzone.net/foros` | España alta | Media — cambia más |
| Reddit `/r/homelab` (search por flair) | Mundial | Baja — JSON API API key, formato variable |
| OpenWrt forum (search) | Mundial técnico | Media |

**Por qué se difiere**: scraping de webs públicas requiere diseño
cuidadoso (rate limit, user-agent honesto, robots.txt, mantenimiento
cuando el HTML cambia). Mejor probar la mecánica de issues primero,
después abordar este lado con su propio plan dedicado.

**Cuando se aborde Fase 2**: la tool va contra fuentes ES primero
(bandaancha + adslzone — son las que el operador conoce). Para ampliar
scope a otros países, cada nuevo país añade su fuente equivalente
(Portugal: pplware, dudasti; Italia: hwupgrade; etc.) — siempre como
adapters separados detrás de la misma interfaz, no scraping monolítico.

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
Pre-step: Helper get_github_token() en core/secrets.py + tests.
          Convención env: MIMIR_GITHUB_TOKEN > GITHUB_TOKEN > None.
          Útil para gpon ahora y futuros plugins. (~15 min)

Step 1:   Inventario read-only — pytest plugins/gpon/ -v.
          Listar tests/tools que dependen del Jekyll clonado. (~10 min)

Step 2:   Snapshot one-shot _isp/ → data/operators.json.
          Script ad-hoc parseando los .md del Jekyll actual antes de
          borrarlo. Mantener estructura JSON propuesta arriba. (~30 min)

Step 3:   Crear plugins/gpon/gpon_mcp/issues.py + tests con mocks.
          Funciones: search_issues, get_issue, search_stick_issues,
          get_module_wiki. Usa core.secrets.get_github_token(). (~60 min)

Step 4:   Wire en server.py de las 5 tools nuevas:
          - gpon_search_issues
          - gpon_get_issue
          - gpon_search_stick_issues
          - gpon_get_module_wiki
          - gpon_get_module_full (combina las 3 fuentes)
          Eliminar gpon_sync_knowledge_base + gpon_get_module_docs.
          Adaptar gpon_list_known_operators para leer operators.json. (~45 min)

Step 5:   Eliminar carpeta knowledge/hack-gpon/, knowledge_sync.py,
          simplificar knowledge_sync_v3.py. Adaptar tests rotos. (~30 min)

Step 6:   Bump version 2.1.0 → 2.2.0. plugin.toml: añadir credential
          opcional GPON_GITHUB_TOKEN. CHANGELOG entry. (~15 min)

Step 7:   Suite verde + smoke test end-to-end real:
          - gpon_search_issues("vlan huawei") → resultados
          - gpon_get_issue(328) → body + comments del issue
          - gpon_search_stick_issues("huawei-hg8120c") → matches
          - gpon_get_module_full("huawei-hg8120c") → 3 fuentes
          - gpon_list_known_operators() → JSON con N entries (~15 min)

Step 8:   Commit local en mimir-mcp + push (autorizado).
          PARAR aquí. Pedir OK explícito antes de tocar
          CTRQuko/gpon-mcp upstream.
```

Total estimado: ~3.5h efectivas (gated por tu OK en cada commit).

## Fuera de scope explícitamente diferido

- **Fase 2 operadores web scraping** (bandaancha/adslzone) — plan
  separado tras validar la mecánica de issues.
- **Repo upstream `CTRQuko/gpon-mcp` push** — Step 9 implícito,
  requiere OK explícito tuyo después de validar local.

---

## Decisiones cerradas (operador confirmó)

1. **`gpon_list_known_operators`**: ✅ MANTENER. Fuente: snapshot inicial
   `data/operators.json` (Fase 1) extraído del Jekyll antes de borrarlo.
   Ampliación a scraping web (bandaancha/adslzone) → Fase 2 diferida con
   plan dedicado.

2. **Token GitHub**: ✅ Helper centralizado en `core/secrets.get_github_token()`
   (mimir-wide), no por plugin. Convención env:
   `MIMIR_GITHUB_TOKEN` > `GITHUB_TOKEN` > None. Plugins lo importan.
   Sin token funciona (10 search/min), con token va mejor (30/min).

3. **`gpon_get_module_docs`**: ✅ ELIMINAR + sustituir por
   `gpon_get_module_full(slug)` que combina `model_details + wiki + issues`.
   Esta tool es la pensada para "crear entrada de stick con info completa".

4. **Repo upstream `CTRQuko/gpon-mcp`**: ✅ LOCAL PRIMERO. Trabajar en
   `plugins/gpon/` de mimir-mcp, validar end-to-end. Push a upstream
   solo tras OK explícito post-validación.

5. **`gpon_search_pulls`**: ✅ DESCARTADA. El repo `hack-gpon.github.io`
   es sitio Jekyll de docs, no código. PRs son cambios de doc, no fixes
   de firmware. Valor real está en issues → suficiente.

## Extensiones añadidas tras feedback

- **`gpon_search_stick_issues(slug)`**: las issues están atadas a configs
  de stick específico, no a operadores. Búsqueda primaria por slug.
- **`gpon_get_module_wiki(slug)`**: raw markdown del Jekyll vía raw fetch.
- **`gpon_get_module_full(slug)`**: integra los 3 a una sola llamada.

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
