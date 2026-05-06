# Plan — Token consumption profiler (mimir framework v0.6.0)

**Estado**: borrador de diseño. NO implementar ahora — desarrollar el
diseño hasta que el operador lo apruebe.
**Fecha**: 2026-05-06.
**Ámbito**: `core/` del framework mimir-mcp + posible CLI nuevo.

---

## Context

Durante el desarrollo activo de mimir + plugins, las tools que devuelven
respuestas grandes consumen contexto del LLM rápido. Hoy no hay forma
de saber, en frío, **qué tools son las gordas** y dónde merecería la
pena recortar/paginar/truncar la respuesta.

El operador propone: medir consumo en una capa observable durante dev,
con el objetivo de **adelgazar las tools voraces** — sin adivinar, con
datos.

Este plan diseña el sistema. La implementación es separada y posterior,
cuando el diseño quede aprobado.

---

## Qué responde el profiler

Preguntas que un operador o developer querría poder responder en frío:

1. ¿Cuántos tokens devolvió mi sesión de hoy en total?
2. ¿Qué tool fue la más cara (single-call max)?
3. ¿Qué tool acumula más tokens (call count × avg size)?
4. ¿Hay tools que se invocan mucho y devuelven mucho? (los `O(n²)`)
5. ¿Cuántas veces el response superó un umbral (e.g. 1000 tokens)?
6. ¿Qué plugins son más caros en agregado?

---

## Diseño

### Pieza 1 — Enriquecer audit log con bytes/tokens

`core/audit.py` ya registra `duration_ms` por tool call. Añadimos dos
campos:

```json
{
  "ts": ...,
  "plugin": "homelab",
  "tool": "list_lxc",
  "args_hash": "...",
  "duration_ms": 230.5,
  "status": "ok",
  "client": "claude-code",
  "response_bytes": 4823,        // ← nuevo
  "response_tokens_est": 1206    // ← nuevo (heurística bytes/4)
}
```

Heurística `tokens_est = bytes // 4` es la que usa la familia GPT-3
para texto inglés. Para JSON estructurado y español es algo conservador
pero suficiente para clasificación relativa (no absoluta).

Mejora futura: `tiktoken` o equivalente para conteo real, pero añade
dependencia. La heurística cubre el caso de uso (priorizar tools, no
calcular billing exacto).

### Pieza 2 — Capturar response_bytes en el wrapper `_timed`

`router.py:_timed` ya envuelve cada tool call. Captura `result`,
serializa con `json.dumps`, mide `len()`:

```python
def _timed(tool_name, fn, args_for_audit):
    start = time.monotonic()
    try:
        result = fn()
        # NUEVO: medir el response antes de devolver
        try:
            payload = json.dumps(result, default=str, ensure_ascii=False)
            response_bytes = len(payload.encode("utf-8"))
        except Exception:
            response_bytes = -1  # serialization-resistant; no medible
        _audit(
            tool_name,
            args_for_audit,
            (time.monotonic() - start) * 1000,
            "ok",
            response_bytes=response_bytes,
        )
        return result
    except Exception as exc:
        _audit(..., error_message=str(exc))
        raise
```

Mismo principio para `_register_skill_tool._fn`,
`_register_agent_tool._fn`, `_setup`. Y para los tools de plugin
mounted vía proxy: el FastMCP middleware tiene un hook `on_call_tool`
que ya filtra whitelist (Fase 6c) — extender para capturar response.

Coste: ~10 LOC en el wrapper, JSON serialize que ya hace FastMCP de
todas formas. Cero impacto al usuario final.

### Pieza 3 — Script `scripts/profile_tools.py`

Lee audit log y reporta. Análogo a `audit_to_runtime_issues.py` —
otro consumidor del mismo audit log estructurado.

```bash
# Resumen de la última hora
uv run python scripts/profile_tools.py --since "1 hour ago"

# Solo errores y top tools por agregado
uv run python scripts/profile_tools.py --since "1 day ago" --top 20

# Output JSON para procesar
uv run python scripts/profile_tools.py --since "1 week ago" --json
```

Output ASCII por defecto:

```
=== Mimir Token Consumption Profile ===
Window: 2026-05-06 00:00:00 → 2026-05-06 11:30:00 (11.5h)
Total calls: 247  |  Total tokens: 142,830  |  Avg/call: 578

Top tools by AGGREGATE token consumption:
  Tokens   Calls  Avg     Max   Plugin/Tool
  ──────  ──────  ────  ──────  ──────────────────────────────
  48,231      12  4019  12,300  homelab.list_lxc
  35,422      28  1265   3,400  gpon.get_module_full
  18,000     180   100     350  router.router_status
  ...

Top tools by SINGLE-CALL MAX:
  Max     Plugin/Tool                Args_hash
  ──────  ─────────────────────────  ─────────
  12,300  homelab.list_lxc           a1b2c3d4
   8,200  homelab.get_node_status    e5f6...

Tools exceeding warning threshold (>1000 tokens):
  homelab.list_lxc:        12 calls, 8 over threshold
  gpon.get_module_full:    28 calls, 14 over threshold
```

### Pieza 4 — Threshold + log warning runtime (opcional)

Env var `MIMIR_RESPONSE_WARN_TOKENS=1000` (default deshabilitado).
Si está set: `_timed` emite `_log.warning(...)` cuando una tool
devuelve > N tokens estimados, con sugerencia:

```
[mimir] WARN: tool 'homelab.list_lxc' returned 4019 tokens (12300
bytes). Consider pagination or filtering. Args hash: a1b2c3d4.
```

Útil en desarrollo activo para ver alertas en stderr sin tener que
correr el profiler manualmente.

### Pieza 5 — Test que se rompe si una tool clave engorda

Idea adicional del operador: "test de consumo en fases dev".

Test de regresión en `tests/test_tool_response_size.py`:

```python
# Marca las tools "pesadas conocidas" con su budget actual.
# Si en futuro el response crece >X%, falla el test → señal de que
# alguien introdujo bloat.

KNOWN_TOOL_BUDGETS_TOKENS = {
    "homelab.list_lxc":        2500,   # +20% margin sobre size típico
    "homelab.list_qemu":       1500,
    "homelab.list_nodes":       300,
    "router.router_help":      4000,   # devuelve doc completa
    "router.router_status":     500,
    "router.router_list_plugins": 800,
}

def test_tool_response_within_budget():
    # Llama cada tool con args dummy via FastMCP test harness,
    # mide response, compara contra budget. FALLA si supera.
    ...
```

Esto convierte el bloat en un fallo de CI visible, no un descubrimiento
post-mortem.

Coste: ~80 LOC + dependencia FastMCP para invocar tools en test.
Probablemente integration test lento (~10s), marcado con
`@pytest.mark.integration` para excluir del run rápido.

---

## Implementación por fases (cuando se apruebe)

```
Fase A — Plumbing observable:
  - Añadir response_bytes + response_tokens_est al audit entry.
  - Capturar en _timed y en los register_*_tool.
  - Tests en test_core_audit.py (validar campos, edge cases).
  Coste: 1.5h.

Fase B — Profiler script:
  - scripts/profile_tools.py + tests.
  - Lee audit log, agrupa, renderiza ASCII y JSON.
  - Reusa lógica de iter_error_entries de audit_to_runtime.
  Coste: 1.5h.

Fase C — Warning runtime opcional:
  - Env var threshold + _log.warning en _timed.
  Coste: 30 min.

Fase D — Test de regresión por budget:
  - tests/test_tool_response_size.py con FastMCP harness.
  - Calibrar budgets iniciales con audit log real de varios días.
  Coste: 2h iniciales + mantenimiento (ajustar budgets cuando una
  tool legítimamente crece).

Total estimado: ~5.5h en 4 fases independientes. Cada fase commiteable
sola.
```

---

## Open questions (para cuando se desarrolle)

1. **Heurística vs `tiktoken`**: ¿bytes/4 (aproximado, sin deps) o
   `tiktoken` (preciso, +dep)?
   - Recomiendo heurística para Fase A. tiktoken solo si el operador
     necesita números absolutos para billing comparison.

2. **Budgets iniciales**: ¿calibrar con audit log existente de los
   últimos N días, o empezar con números a ojo y ajustar?
   - Recomiendo: tras Fase A, dejar el sistema correr 1 semana,
     después calibrar Fase D con datos reales.

3. **Granularidad del warning**: ¿por call individual, por agregado/min,
   o por sesión?
   - Sugiero: empezar por call (Fase C). Si genera ruido en stderr,
     subir granularidad.

4. **Args en el reporte**: ¿exponer args_hash en el output del profiler
   ayuda a debuggear bloat por inputs específicos? (e.g. `list_lxc` con
   filtros distintos puede devolver tamaños muy distintos)
   - Sí, como hash + (opcionalmente) los args sanitizados que ya
     tenemos en audit error entries.

5. **CI integration**: ¿el test de Fase D corre en cada PR (lento, ~10s)
   o solo en branch main / nightly?
   - Recomiendo: marcar `integration`, correr en nightly. PR rápido
     no bloqueado.

---

## Por qué este sistema y no otro

Alternativas consideradas:

| Alternativa | Por qué la descarto |
|-------------|--------------------|
| Profiler de Python (`cProfile`) | Mide tiempo, no payload. Distinto problema |
| Logging manual con `print(len(json.dumps(...)))` | Reactivo, no estructurado, no sumable |
| Dashboard externo (Grafana, etc.) | Overkill, dependencia infra. El audit log JSONL ya es queryable |
| FastMCP middleware en cada plugin | Distribuye lógica que ya está centralizada en `_timed` |

El diseño aprovecha la infraestructura existente (audit log estructurado +
patterns de scripts que ya hicimos en v0.5.0). Coste marginal bajo.

---

## Próximo paso al desarrollar

1. Operador responde a las 5 open questions.
2. Implementa Fase A (plumbing) en commit aparte.
3. Tras 1 semana de datos reales, ataja Fases B y D con budgets
   calibrados.
4. Fase C es opt-in según criterio operador.

Se incorpora a v0.6.0 del framework cuando A+B estén listas. C y D
pueden quedarse en v0.6.x patches.
