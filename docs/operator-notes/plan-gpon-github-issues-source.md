# Plan — Plugin gpon: sustituir sitio web por GitHub Issues como fuente de fixes

**Estado**: borrador, no ejecutar hasta OK del operador.
**Fecha**: 2026-05-06.
**Ámbito**: `plugins/gpon/` dentro de `mimir-mcp` (repo upstream
`CTRQuko/gpon-mcp`).

---

## Context

El plugin gpon hoy **arrastra el sitio Jekyll completo** de
`hack-gpon/hack-gpon.github.io` como fuente de conocimiento (modelos,
firmwares, comandos por operador). El operador quiere:

1. **Mantener** los modelos/firmwares como fuente útil, pero sin clonar
   el sitio entero.
2. **Reducir la huella**: dejar solo lo que aporta valor de operación.
3. **Añadir Issues del repo** como nueva fuente — porque ahí aparecen
   *fixes* y *workarounds* de la comunidad que no están publicados en el
   sitio.

El cambio tiene tres motivos prácticos:

- El clon Jekyll pesa MB y se queda obsoleto entre `git pull`s.
- Los issues del repo capturan problemas reales que el operador o yo
  podemos resolver mejor leyendo, sin scraping del sitio web.
- Al separar la lógica, el plugin queda más mantenible.

---

## Estado actual

### Fuentes activas en el plugin

| Fuente | Path | Cómo se usa |
|--------|------|-------------|
| `data/models.json` | `plugins/gpon/gpon_mcp/data/models.json` | Snapshot 2026-04-10, 54 modelos. Carga al iniciar `HackGPONAPI`. **Útil — se queda.** |
| Clon Jekyll completo | `plugins/gpon/gpon_mcp/knowledge/hack-gpon/` (con `.git/`, `_ont/`, `_gpon/`, `_isp/`, `_config.yml`) | `knowledge_sync.py:sync_knowledge()` hace `git clone` o `git pull` del repo entero. **Se elimina.** |
| Raw GitHub fetch puntual | `knowledge_sync_v3.py:HackGPONAPI` | Fetch a `raw.githubusercontent.com/.../<path>` con cache 5min. **Se simplifica o se queda en lo mínimo.** |

### Tools MCP que dependen del Jekyll clonado

Tras inspección de `gpon_mcp/server.py` (línea 488 en adelante):

| Tool MCP | Función | Depende de... |
|----------|---------|---------------|
| `gpon_sync_knowledge_base(force)` | git pull del Jekyll | clon completo |
| `gpon_get_module_docs(model_slug)` | Devuelve docs Jekyll del modelo | files en `knowledge/hack-gpon/_ont/` |
| `gpon_search_models(query)` | Busca en índice + Jekyll | parcial |
| `gpon_get_model_details(slug)` | Detalle de modelo | mezclado: data/models.json + raw GitHub fetch |
| `gpon_list_known_modules()` | Lista catálogo | data/models.json (no Jekyll) |
| `gpon_list_known_operators()` | Lista ISPs | files Jekyll `_isp/` |
| `gpon_list_known_modules` | (idem que arriba) | data/models.json |

Las llamadas que sólo usan `data/models.json` siguen funcionando sin el
clon. Las que leen `knowledge/hack-gpon/_ont/` o `_isp/` se rompen si lo
quitamos — necesitan migrar.

### Tests afectados

- `plugins/gpon/tests/test_knowledge_integration.py` (~150 LOC) — valida
  flujo de carga y MCP tools.
- `plugins/gpon/tests/test_persistence.py` (17 tests) — sticks
  persistence, NO tocado por este cambio.

---

## Estado objetivo

### Fuentes mantenidas

1. **`data/models.json`** — sigue como índice principal de modelos.
   Periodicidad: actualización manual (no auto sync).
2. **Raw GitHub fetch puntual** (mínimo) — solo para detalles de un
   modelo específico cuando el índice local no tiene la info. Cache.
3. **NUEVA: GitHub Issues** del repo `hack-gpon/hack-gpon.github.io` —
   búsqueda y consulta de issues.

### Fuentes eliminadas

- Clon Jekyll completo en `knowledge/hack-gpon/` (carpeta + git).
- `gpon_sync_knowledge_base` tool (se reemplaza por `gpon_sync_issues` —
  ver abajo).
- Docs de operadores que dependen de Jekyll `_isp/` — alternativa: mover
  esa info a `data/operators.json` snapshot manual.

### Nuevas tools MCP

| Tool nuevo | Propósito | Args |
|-----------|-----------|------|
| `gpon_search_issues(query, state="all", labels=None, limit=20)` | Busca en issues del repo. State filtra `open`/`closed`/`all`. | query: str, state, labels, limit |
| `gpon_get_issue(number)` | Detalle de un issue (body + comments). | number: int |
| `gpon_sync_issues(force=False)` | Refresca cache local de issues. Default: solo si caché >24h o `force`. | force: bool |
| `gpon_list_issue_labels()` | Lista labels disponibles para filtrar searches. | — |

Las tools **eliminadas**: `gpon_sync_knowledge_base`, `gpon_get_module_docs`,
`gpon_list_known_operators` (esta última sólo si la migración a JSON no
se hace; ver abajo).

### Nuevo módulo: `gpon_mcp/issues.py`

Implementa fetch y caché de issues vía GitHub REST API:

- Endpoint: `GET https://api.github.com/repos/hack-gpon/hack-gpon.github.io/issues`
- Auth: opcional (token GitHub PAT) — sin token, rate limit 60 req/h.
  Con token, 5000 req/h. El plugin acepta `GPON_GITHUB_TOKEN` como
  credencial opcional en `plugin.toml`.
- Caché: archivo JSONL en `plugins/gpon/gpon_mcp/data/issues_cache.jsonl`
  con TTL 24h por defecto.
- Búsqueda: full-text contra `title + body + comments` cargado en memoria
  desde el cache.

### Esquema del cache

```json
{
  "synced_at": 1715000000.0,
  "repo": "hack-gpon/hack-gpon.github.io",
  "issues": [
    {
      "number": 42,
      "title": "...",
      "state": "open|closed",
      "labels": ["bug", "fix"],
      "body": "...",
      "comments": [{"user": "x", "body": "..."}],
      "created_at": "2026-...",
      "updated_at": "2026-..."
    }
  ]
}
```

---

## Migration steps

Pasos en orden, gated por OK del operador en cada uno:

### Step 1 — Inventariar lo que se rompe (read-only, ~15 min)

Antes de borrar nada, ejecutar todos los tests del plugin gpon
(`pytest plugins/gpon/tests/`) y registrar qué pasa actualmente.

Identificar callsites que dependan de `knowledge/hack-gpon/_isp/` o
`_ont/` directamente. Listar para tomar decisiones.

### Step 2 — Snapshot de `_isp/` a JSON (~30 min, opcional)

Si `gpon_list_known_operators` se quiere mantener:

- Script one-off `scripts/snapshot_isp_to_json.py` que recorre el clon
  Jekyll actual y escribe `plugins/gpon/gpon_mcp/data/operators.json`.
- Es un snapshot — no se actualiza automáticamente, igual que
  `models.json`. Periodicidad: manual cuando se necesite.

Si la tool no se usa, saltar este step y eliminarla.

### Step 3 — Implementar `gpon_mcp/issues.py` (~1h)

- Fetch issues vía GitHub API (urllib stdlib o `requests` ya disponible).
- Caché JSONL en `data/issues_cache.jsonl`.
- 4 funciones públicas: `fetch_issues()`, `search_issues()`,
  `get_issue()`, `list_labels()`.
- Soporte token opcional (`GPON_GITHUB_TOKEN`).
- Tests en `tests/test_issues.py` con mocks de la API GitHub.

### Step 4 — Wire de tools MCP en `server.py` (~30 min)

- Añadir 4 tools nuevas (`gpon_search_issues`, `gpon_get_issue`,
  `gpon_sync_issues`, `gpon_list_issue_labels`).
- Eliminar tools obsoletas: `gpon_sync_knowledge_base`, `gpon_get_module_docs`.
- Adaptar `gpon_search_models` y `gpon_get_model_details` para que
  usen sólo `data/models.json` + raw GitHub fetch (eliminar dependencia
  Jekyll).

### Step 5 — Eliminar el clon Jekyll (~10 min)

- Borrar `plugins/gpon/gpon_mcp/knowledge/hack-gpon/`.
- Borrar `plugins/gpon/gpon_mcp/knowledge_sync.py` (toda la lógica de
  clon/pull se va).
- Simplificar `knowledge_sync_v3.py` para que sólo maneje el raw fetch
  puntual (o renombrar a `models_api.py` para reflejar el scope reducido).

### Step 6 — Actualizar `plugin.toml` y manifest (~10 min)

- Añadir credential opcional `GPON_GITHUB_TOKEN` en `[security].credential_refs`.
- Bump versión del plugin: 2.1.0 → 2.2.0.
- CHANGELOG con resumen de cambio.

### Step 7 — Tests verde + docs (~30 min)

- Suite gpon completa: añadir los nuevos `test_issues.py`, ajustar
  los que se rompan por la eliminación de tools obsoletas.
- README.md del plugin: documentar las 4 tools nuevas + cómo configurar
  el token GitHub (opcional).

### Step 8 — Commit + push (PR upstream)

Como el plugin vive en repo separado (`CTRQuko/gpon-mcp`), el cambio
requiere:

- Commits locales en `plugins/gpon/` que reflejan el cambio (esto sí lo
  hago en mimir-mcp).
- PR (o push directo si el operador lo hace) al repo upstream
  `CTRQuko/gpon-mcp` con los mismos cambios. **Requiere OK explícito del
  operador para tocar repo externo.**

---

## Open questions (decisiones del operador)

Antes de ejecutar, necesito que confirmes:

1. **`_isp/` data**: ¿se hace snapshot a JSON (Step 2) o se elimina la
   tool `gpon_list_known_operators`?
2. **Token GitHub**: ¿quieres usar tu PAT existente
   (`C:\homelab\.config\secrets\github-token.md` — vi referencia en
   `core-security.md`) o creas uno scoped solo para repo público?
   *Read-only de un repo público probablemente no necesita token, pero
   evita rate limits 60/h*.
3. **`gpon_get_module_docs`**: ¿la usas en algún flujo? Si sí, ¿qué
   sustituye? ¿Búsqueda en issues?
4. **Polling de issues**: ¿auto-sync cada 24h vía cron (igual que el
   audit-bridge), manual con `gpon_sync_issues(force=True)`, o ambos?
5. **Repo upstream `CTRQuko/gpon-mcp`**: ¿este cambio se hace
   directamente en upstream o trabajamos en una rama del fork local
   primero?

---

## Verification end-to-end (post-implementación)

Cuando se ejecute, validamos:

1. `pytest plugins/gpon/tests/ -v` → 100% verde con tests nuevos.
2. `python -c "from plugins.gpon.gpon_mcp.issues import fetch_issues; print(len(fetch_issues()))"` → devuelve N>0 issues.
3. Llamar `gpon_search_issues(query="lantiq factory reset")` → devuelve resultados con `number`, `title`, `body[:200]`.
4. Llamar `gpon_get_issue(number=<algo conocido>)` → devuelve full body + comments.
5. Confirmar que `plugins/gpon/gpon_mcp/knowledge/` ya no existe (carpeta eliminada).
6. `du -sh plugins/gpon/` → tamaño reducido (antes con `.git/` del clon ~MB; después solo código + JSON).
7. Plugin arranca sin crashes con `MIMIR_LOG_LEVEL=debug router --dry-run`.

---

## Riesgos / mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Eliminar `gpon_get_module_docs` rompe flujo del operador | Confirmar uso real (Q3) antes de eliminar; si se usa, reemplazar por búsqueda en issues. |
| GitHub API rate limit con sync frecuente | Cache 24h por defecto; token opcional; respeto de `X-RateLimit-Remaining` con backoff. |
| Issues del repo no son representativos de fixes reales | Filtrado por label (`bug`, `fix`, `workaround`) en `search_issues`; el operador valida con muestreo en Step 3. |
| Romper tests existentes al simplificar `knowledge_sync_v3` | Step 1 inventario + tests tras cada step; rollback fácil porque Steps 3-5 son aditivos. |
| Repo upstream `CTRQuko/gpon-mcp` desincronizado del local | Step 8 requiere OK explícito; alternativa: trabajar primero en local, validar, luego push upstream. |

---

## Próximo paso al aprobar

1. Operador responde a Open questions 1-5.
2. Ejecuto Step 1 (inventario read-only) y reporto qué se rompe.
3. Itero Steps 2-8 con commits incrementales y push automático (autorización ya dada para mimir-mcp).
