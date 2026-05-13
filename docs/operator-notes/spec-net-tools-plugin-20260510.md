# Spec funcional — plugin `net-tools` (Cloudflare + AdGuard)

> **Actualización 2026-05-13**: Pi-hole DESCARTADO del scope (decisión
> operador: no es target del homelab a futuro). El plugin agregado
> ahora incluye 2 sub-módulos (`cloudflare/`, `adguard/`), 11 tools
> en total (5 cloudflare + 6 adguard).
>
> La sección 5 (Pi-hole) se mantiene como REFERENCIA HISTÓRICA / no-implementada
> con banner explícito al inicio. No la borramos por si el target cambia.
>
> Especificación de las áreas P0 del documento `tool-gaps-reverse-proxy-plan-20260510.md`.
> Plugin agregado en `mimir-mcp/plugins/net-tools/` con sub-módulos
> compartiendo runtime + manifest. Nivel: spec funcional — signatures,
> params, returns, validations, edge cases, errores. NO incluye
> implementación.

---

## 0. Context y scope

**Cubre**:
- DNS de la zona Cloudflare `casaredes.cc` (records CRUD)
- Pi-hole REST API v6 multi-instancia (TS L1 + VPS + Munilla)
- AdGuard Home REST API multi-instancia (L1 + L2 cuando exista)

**NO cubre**:
- Cloudflare Workers, Pages, Tunnels, R2, Stream
- Pi-hole groups/clients/adlists (solo `custom_dns` + `config` + `reload`)
- AdGuard `parental control`, `safe browsing`, `stats` avanzadas

**Asunciones**:
- Tokens viven en `C:/homelab/.config/secrets/*.md` referenciados por env vars
- Pi-hole API v6 (no v5 — schemas distintos)
- AdGuard ≥ v0.107
- Cloudflare API v4 (estable desde 2017)

---

## 1. Estructura del plugin

```
plugins/net-tools/
├── plugin.toml
├── pyproject.toml
├── net_tools/
│   ├── __init__.py
│   ├── server.py            # FastMCP entry, mutation gate, registra 3 sub-módulos
│   ├── models.py            # Pydantic shared (DnsRecord, PiholeConfig, etc.)
│   ├── errors.py            # NetToolsError jerárquico
│   ├── http_client.py       # httpx wrapper con retries, auth, mask logging
│   ├── cloudflare/
│   │   ├── __init__.py
│   │   ├── tools.py         # 6 tools cloudflare_dns_*
│   │   └── client.py        # Cloudflare API client
│   ├── pihole/
│   │   ├── __init__.py
│   │   ├── tools.py         # 8 tools pihole_*
│   │   ├── client.py        # Pi-hole API v6 client (auto-login, SID)
│   │   └── instances.py     # multi-instance resolver via inventory
│   └── adguard/
│       ├── __init__.py
│       ├── tools.py         # 6 tools adguard_*
│       └── client.py        # AdGuard REST client (Basic Auth)
└── tests/
    ├── test_cloudflare.py
    ├── test_pihole.py
    └── test_adguard.py
```

---

## 2. plugin.toml

```toml
[plugin]
name = "net-tools"
version = "0.1.0"
enabled = false   # operator opts in tras setear creds

[runtime]
entry = "server.py"
python = ">=3.11"
deps = ["httpx>=0.28", "pydantic>=2.5", "pyyaml>=6.0"]
venv = "auto"

[security]
allow_mutations = false   # gating principal
credential_refs = [
  # Cloudflare
  "CLOUDFLARE_TOKEN", "CLOUDFLARE_ZONE_ID",
  # Pi-hole multi-instance
  "PIHOLE_*_HOST", "PIHOLE_*_PASSWORD",
  # AdGuard multi-instance
  "ADGUARD_*_HOST", "ADGUARD_*_USER", "ADGUARD_*_PASSWORD",
]

[[requires.credentials]]
pattern = "CLOUDFLARE_TOKEN"
prompt = "Cloudflare API token con scope Zone.DNS:Edit sobre la zona objetivo."

[[requires.credentials]]
pattern = "CLOUDFLARE_ZONE_ID"
prompt = "Zone ID de la zona Cloudflare (panel CF → Overview → derecha)."
```

---

## 3. Patrones transversales

### 3.1 Mutation gating

Lectura siempre disponible. Tools que escriben (POST/PATCH/PUT/DELETE) sólo
se registran si `[security].allow_mutations == true`. Mismo patrón que
nginx-ui-ops v0.3.0.

### 3.2 Multi-instance (Pi-hole + AdGuard)

Las instancias se modelan como **hosts del inventory** con `tag` discriminador:

```yaml
# inventory/hosts.yaml
hosts:
  - name: pihole-tsl1
    type: generic
    address: 100.99.189.118
    port: 80
    tags: [pihole, tailscale]
    auth: { method: password, credential_ref: PIHOLE_TSL1_PASSWORD }
  - name: pihole-vps
    type: generic
    address: 100.69.126.35
    port: 80
    tags: [pihole, vps, hetzner]
    auth: { method: password, credential_ref: PIHOLE_VPS_PASSWORD }
  - name: pihole-mun
    type: generic
    address: 192.168.2.50
    port: 80
    tags: [pihole, munilla]
    auth: { method: password, credential_ref: PIHOLE_MUN_PASSWORD }
  - name: adguard-l1
    type: generic
    address: 10.0.1.14
    port: 3000
    tags: [adguard]
    auth: { method: basic, credential_ref: ADGUARD_L1_PASSWORD }
```

Cada tool toma `host_ref: str` que **resuelve via** `core.inventory.resolve_host(host_ref)`.
Validación: si el host no tiene tag esperado (`pihole`, `adguard`), error.

### 3.3 Error model

```python
class NetToolsError(Exception):
    """Base — no instanciar directamente."""

class AuthError(NetToolsError):
    """401 / 403 / SID inválido / token expirado."""

class NotFoundError(NetToolsError):
    """Resource no existe (record_id, instance, etc.)."""

class ValidationError(NetToolsError):
    """Pre-flight reject (zone wrong, proxied=true en zona privada, etc.)."""

class UpstreamError(NetToolsError):
    """5xx del backend, timeout, response malformado."""

class IdempotencyError(NetToolsError):
    """Resource ya existe con valor distinto al pedido — caller decide."""
```

Tools devuelven `dict` con shape:
```python
{
  "ok": bool,
  "data": ...,           # presente si ok=True
  "error": str,          # presente si ok=False
  "error_type": str,     # "auth" | "not_found" | "validation" | "upstream" | "idempotency"
  "context": dict,       # extra info: instance, request_id, etc.
}
```

### 3.4 Logging y secret masking

- `http_client.py` registra `mask(token)` → `M51p9***iCi` (primeros 5 + últimos 3)
- Headers `Authorization`, `Cookie`, `x-api-key` → `***REDACTED***`
- Nunca loggear el body de un POST/PATCH si contiene `password`/`token`/`api_key`

### 3.5 Idempotencia

Cada tool de **mutación** documenta su comportamiento:
- `*_create_*`: 409 si ya existe → `IdempotencyError` (caller usa `*_update_*`)
- `*_update_*`: 404 si no existe → `NotFoundError`
- `*_delete_*`: 404 si no existe → `ok=true, action="already_absent"` (idempotente)
- `*_set_*` (atómico): replace completo, idempotente por naturaleza

### 3.6 Reintentos

httpx con backoff exponencial 100ms→500ms→2s sólo para:
- Connection errors (transient)
- 502/503/504 (upstream gateway issues)
- 429 (rate limit) con respeto a `Retry-After`

NO reintentar 4xx semánticos (400, 401, 403, 404, 422).

---

## 4. Sección Cloudflare DNS — 6 tools

### 4.1 `cloudflare_dns_list_records`

```
@mcp.tool()  # ALWAYS-ON
def cloudflare_dns_list_records(
    name_filter: str | None = None,    # match por substring del name
    type_filter: str | None = None,    # "A" | "AAAA" | "CNAME" | "TXT" | "MX" | "NS"
    proxied_only: bool | None = None,  # filtra por proxied=true|false
) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "zone_id": "abc123",
    "zone_name": "casaredes.cc",
    "records": [
      DnsRecord(
        id="789def",
        name="www.casaredes.cc",
        type="A",
        content="100.69.126.35",
        proxied=False,
        ttl=1,           # 1 = automatic
        comment="VPS Hetzner — 2026-05-04",
      ),
      ...
    ],
    "count": 47,
  },
}
```

**Validations**:
- `type_filter` debe estar en set válido o None
- `name_filter` se hace lowercase compare

**Errores documentados**: `AuthError` (token inválido), `UpstreamError` (CF API down).

---

### 4.2 `cloudflare_dns_create_record`

```
@mcp.tool()  # GATED
def cloudflare_dns_create_record(
    name: str,
    type: str,                         # "A" | "AAAA" | "CNAME" | "TXT" | "MX"
    content: str,
    ttl: int = 1,                       # 1 = automatic; 60-86400 manual
    proxied: bool = False,              # forced False — ver 4.2.1
    comment: str | None = None,
    priority: int | None = None,        # solo MX
    confirm: bool = False,              # safety guard
) -> dict
```

**Returns** (success):
```python
{
  "ok": True,
  "data": {
    "id": "newid123",
    "name": "newhost.casaredes.cc",
    "action": "created",
  },
}
```

**Validations pre-flight**:
1. `confirm=True` obligatorio (sino → `ValidationError`)
2. `name` debe terminar en `.casaredes.cc` o ser igual a `casaredes.cc` (zone match)
3. `type` ∈ {A, AAAA, CNAME, TXT, MX}
4. **`proxied` SOLO acepta `False`** (ADR-0002 del homelab — ver razón abajo). Si caller pasa `True` → `ValidationError`.
5. `content` valida shape según type:
   - A: IPv4 dotted (regex `^\d{1,3}(\.\d{1,3}){3}$`)
   - AAAA: IPv6
   - CNAME: hostname con dots
   - TXT: any string ≤ 255 chars
6. `ttl` ∈ {1} ∪ [60, 86400]
7. `priority` requerido si type=MX, ignorado en otros

**Idempotencia**: si ya existe record con `name+type` → `IdempotencyError` con `existing_id` en context. Caller usa `update`.

**ADR-0002 reasoning**: `proxied=true` rompe split-DNS interno + ACME DNS-01 + LAN-only invariants. Ver `infra/dns-catalog/docs/architecture.md`.

---

### 4.3 `cloudflare_dns_update_record`

```
@mcp.tool()  # GATED
def cloudflare_dns_update_record(
    record_id: str,                    # de cloudflare_dns_list o get
    content: str | None = None,
    ttl: int | None = None,
    proxied: bool | None = None,        # también forzado a False si se pasa
    comment: str | None = None,
    confirm: bool = False,
) -> dict
```

**Returns**: igual que create con `action: "updated"`.

**Validations**:
- `confirm=True` obligatorio
- Al menos 1 de los `Optional` no-None (sino → no-op = `ValidationError`)
- `proxied=True` → `ValidationError`
- `record_id` debe existir → si 404, `NotFoundError`

---

### 4.4 `cloudflare_dns_delete_record`

```
@mcp.tool()  # GATED
def cloudflare_dns_delete_record(
    record_id: str,
    confirm: bool = False,
) -> dict
```

**Returns**:
- Si existía: `{ok: true, data: {id, action: "deleted"}}`
- Si no existía: `{ok: true, data: {id, action: "already_absent"}}` (idempotente)

**Validations**: `confirm=True` obligatorio.

---

### 4.5 `cloudflare_dns_get_record`

```
@mcp.tool()  # ALWAYS-ON
def cloudflare_dns_get_record(
    name: str,                         # FQDN exacto
    type: str | None = None,           # si None y hay >1 type para name → ambigüedad
) -> dict
```

**Returns**:
- 1 match: `{ok: true, data: DnsRecord}`
- 0 matches: `{ok: false, error: "not_found", error_type: "not_found"}`
- N matches sin `type`: `{ok: false, error: "ambiguous", error_type: "validation", context: {types_found: [...]}}`

**Use case**: lookup por nombre antes de `update` o `delete` (caller no recuerda record_id).

---

### 4.6 `cloudflare_dns_purge_cache` (opcional, P3)

```
@mcp.tool()  # GATED
def cloudflare_dns_purge_cache(
    files: list[str] | None = None,    # URLs específicas
    everything: bool = False,
    confirm: bool = False,
) -> dict
```

**Validations**: si `everything=True` requiere `confirm=True`. Si ambos None → noop.

NOTA: NO es DNS, es CDN. Solo si se publica algo cacheable (proxied=true) — que el ADR-0002 prohíbe. Mantener por completitud, prioridad baja.

---

## 5. Sección Pi-hole — 8 tools — ❌ DESCARTADO 2026-05-13

> **Operator decision 2026-05-13**: Pi-hole no es target del homelab a
> futuro — la sección queda como REFERENCIA HISTÓRICA / NO-IMPLEMENTADA.
> Se preserva el diseño completo por si la decisión cambia y para que
> el patrón documentado de multi-instance + SID rotativo siga
> disponible como precedente para otros plugins (cualquier backend
> con auth-token rotativo).
>
> **No hay código en `plugins/net-tools/net_tools/pihole/`** (el directorio
> no existe — el plugin v0.1.0 solo tiene `cloudflare/` y `adguard/`).

Pi-hole API v6 usa SID rotativo (1h TTL). El `client.py` mantiene SID en memoria
del proceso, re-loguea automáticamente si expira. No expone `login` como tool.

### 5.1 `pihole_get_status`

```
@mcp.tool()  # ALWAYS-ON
def pihole_get_status(host_ref: str) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "host_ref": "pihole-tsl1",
    "address": "100.99.189.118",
    "version": "v6.0.5",
    "ftl_version": "6.0.4",
    "blocking": "enabled",     # "enabled" | "disabled" | "failed"
    "queries_today": 14823,
    "queries_blocked_today": 4521,
    "uptime_seconds": 86400,
  },
}
```

**Validations**: `host_ref` resuelve a host con tag `pihole`, sino `ValidationError`.

---

### 5.2 `pihole_get_config`

```
@mcp.tool()  # ALWAYS-ON
def pihole_get_config(
    host_ref: str,
    section: str | None = None,        # "dns" | "dhcp" | "misc" | None=all
    detailed: bool = False,             # incluye type/default/help por field
) -> dict
```

**Returns**: dict con la sección pedida o full config si None.

**Edge case**: `detailed=True` para PATCH-safe operations (saber type expected). Útil pre-`update_config`.

---

### 5.3 `pihole_update_config`

```
@mcp.tool()  # GATED
def pihole_update_config(
    host_ref: str,
    section: str,                      # "dns" | "dhcp" | "misc" | etc.
    patch: dict[str, Any],              # campos a modificar (PATCH semantics)
    confirm: bool = False,
) -> dict
```

**Returns**: `{ok: true, data: {section, fields_changed: [...], requires_reload: bool}}`.

**Validations**:
- `confirm=True` obligatorio
- `section` debe existir (validar contra `get_config`)
- `patch` keys deben ser fields válidos del section (warning sobre keys desconocidos, no error — Pi-hole los acepta y los descarta)
- Si `patch` añade `upstreams=[]` → `ValidationError` (no permitir vaciar upstreams)

**Side effect**: Pi-hole v6 algunos cambios requieren reload manual. La tool devuelve `requires_reload=true` para que caller llame `pihole_reload`.

---

### 5.4 `pihole_list_custom_dns`

```
@mcp.tool()  # ALWAYS-ON
def pihole_list_custom_dns(
    host_ref: str,
    name_filter: str | None = None,
) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "host_ref": "pihole-tsl1",
    "records": [
      {"domain": "pve.casaredes.cc", "ip": "10.0.1.2"},
      {"domain": "pve2.casaredes.cc", "ip": "10.0.1.3"},
      ...
    ],
    "count": 23,
  },
}
```

**Implementation note**: en v6 los custom DNS están en `/api/config/dns/hosts` o `/api/dns/hosts` (depende de la version exacta). Client encapsula.

---

### 5.5 `pihole_add_custom_dns`

```
@mcp.tool()  # GATED
def pihole_add_custom_dns(
    host_ref: str,
    domain: str,
    ip: str,
    confirm: bool = False,
    upsert: bool = False,               # si ya existe, update en lugar de error
) -> dict
```

**Returns**: `{ok: true, data: {domain, ip, action: "created"|"updated"|"already_correct"}}`.

**Validations**:
- `confirm=True` obligatorio
- `ip` valida IPv4 o IPv6
- `domain` valida no contiene espacios, ≤253 chars
- Si `upsert=False` y existe con IP distinta → `IdempotencyError`
- Si `upsert=False` y existe con misma IP → `ok=true, action="already_correct"` (idempotente)

---

### 5.6 `pihole_remove_custom_dns`

```
@mcp.tool()  # GATED
def pihole_remove_custom_dns(
    host_ref: str,
    domain: str,
    ip: str | None = None,              # si None, borra todos los records de domain
    confirm: bool = False,
) -> dict
```

**Returns**: `{ok: true, data: {domain, removed: [{ip, ...}], action: "deleted"|"already_absent"}}`.

**Edge case**: un mismo domain puede tener múltiples IPs. `ip=None` borra todos, `ip=specific` borra solo esa entry.

---

### 5.7 `pihole_reload`

```
@mcp.tool()  # GATED
def pihole_reload(
    host_ref: str,
    component: str = "dns",             # "dns" | "lists" | "all"
    confirm: bool = False,
) -> dict
```

**Returns**: `{ok: true, data: {host_ref, component, reloaded_at: "2026-05-10T..."}}`.

**Validations**:
- `confirm=True` obligatorio
- `component` ∈ {dns, lists, all}

---

### 5.8 `pihole_query_log_search`

```
@mcp.tool()  # ALWAYS-ON
def pihole_query_log_search(
    host_ref: str,
    domain_filter: str | None = None,
    client_filter: str | None = None,
    blocked_only: bool = False,
    since_seconds: int = 3600,          # ventana hacia atrás
    limit: int = 100,                   # max 1000
) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "host_ref": "pihole-tsl1",
    "queries": [
      {"ts": "2026-05-10T19:55:00Z", "domain": "x.casaredes.cc",
       "client": "10.0.1.110", "type": "A", "status": "OK", "reply": "10.0.1.40"},
      ...
    ],
    "count": 47,
    "truncated": False,
  },
}
```

**Use case**: debug DNS bucket like the 2026-05-07 madrugada incident.

---

## 6. Sección AdGuard — 6 tools

AdGuard usa Basic Auth (user+password). El client lo añade a cada request.

### 6.1 `adguard_list_rewrites`

```
@mcp.tool()  # ALWAYS-ON
def adguard_list_rewrites(
    host_ref: str,
    domain_filter: str | None = None,
) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "host_ref": "adguard-l1",
    "rewrites": [
      {"domain": "*.apps.casaredes.cc", "answer": "10.0.1.40"},
      ...
    ],
    "count": 14,
  },
}
```

---

### 6.2 `adguard_set_rewrites` (atómico — bulk replace)

```
@mcp.tool()  # GATED
def adguard_set_rewrites(
    host_ref: str,
    rewrites: list[dict],               # [{"domain": "...", "answer": "..."}]
    confirm: bool = False,
    dry_run: bool = False,
) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "host_ref": "adguard-l1",
    "diff": {
      "added": [{"domain": ..., "answer": ...}, ...],
      "removed": [{"domain": ..., "answer": ...}, ...],
      "unchanged": 12,
    },
    "applied": True,                    # False si dry_run
  },
}
```

**Validations**:
- `confirm=True` obligatorio (incluso con dry_run=True para evitar typos)
- Cada item de `rewrites` valida `domain` + `answer` (IPv4 / IPv6 / CNAME)
- Duplicados de domain → `ValidationError`

**Atomicidad**: la operación es **read current → diff → write completo**. Si el write falla a mitad, AdGuard mantiene el estado anterior (la API soporta replace via PUT del array completo).

**Side effect**: NO requiere reload separado. AdGuard recarga rewrites in-memory en el commit.

---

### 6.3 `adguard_add_rewrite`

```
@mcp.tool()  # GATED
def adguard_add_rewrite(
    host_ref: str,
    domain: str,
    answer: str,
    upsert: bool = False,
    confirm: bool = False,
) -> dict
```

**Returns**: `{ok: true, data: {domain, answer, action: "added"|"updated"|"already_correct"}}`.

**Implementation**: helper sobre `adguard_set_rewrites` — read current, append/upsert, write atomic.

**Validations**: igual que `pihole_add_custom_dns` (idempotency + upsert flag).

---

### 6.4 `adguard_remove_rewrite`

```
@mcp.tool()  # GATED
def adguard_remove_rewrite(
    host_ref: str,
    domain: str,
    answer: str | None = None,          # None = remove all answers for domain
    confirm: bool = False,
) -> dict
```

**Returns**: `{ok: true, data: {domain, removed: [...], action: "deleted"|"already_absent"}}`.

**Implementation**: helper sobre `adguard_set_rewrites`.

---

### 6.5 `adguard_list_filtering_rules`

```
@mcp.tool()  # ALWAYS-ON
def adguard_list_filtering_rules(
    host_ref: str,
    enabled_only: bool = False,
    pattern_filter: str | None = None,
) -> dict
```

**Returns**:
```python
{
  "ok": True,
  "data": {
    "host_ref": "adguard-l1",
    "user_rules": [
      "||doubleclick.net^",
      "@@||my-isp-tracker.com^",   # whitelist
      ...
    ],
    "filter_lists": [
      {"id": 1, "name": "AdGuard DNS filter", "enabled": True, "rules_count": 84512},
      ...
    ],
  },
}
```

---

### 6.6 `adguard_query_log_search`

Equivalente a `pihole_query_log_search` para AdGuard. Mismos params + return shape.

---

## 7. Pydantic models compartidos (`net_tools/models.py`)

```python
from pydantic import BaseModel, IPvAnyAddress, Field
from typing import Literal, Optional

class DnsRecord(BaseModel):
    id: str
    name: str
    type: Literal["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV"]
    content: str
    proxied: bool = False
    ttl: int = 1
    comment: Optional[str] = None
    priority: Optional[int] = None       # solo MX/SRV

class CustomDnsEntry(BaseModel):
    domain: str
    ip: str                              # IPv4 o IPv6 — validado custom

class Rewrite(BaseModel):
    domain: str
    answer: str                          # IPv4, IPv6, o CNAME

class PiholeStatus(BaseModel):
    host_ref: str
    address: str
    version: str
    ftl_version: str
    blocking: Literal["enabled", "disabled", "failed"]
    queries_today: int
    queries_blocked_today: int
    uptime_seconds: int

class QueryLogEntry(BaseModel):
    ts: str                              # ISO8601 UTC
    domain: str
    client: str
    type: str
    status: str
    reply: Optional[str] = None
```

---

## 8. Tests mínimos por tool

Cada tool tiene **≥ 3 tests**:

1. **Happy path** — request OK, parse return correcto
2. **Auth failure** — 401 → `AuthError`, no se propaga el token al log
3. **Validation pre-flight** — al menos 1 caso de `ValidationError` (proxied=true en CF, confirm=False, etc.)

Tests adicionales para mutating:
4. **Idempotency** — segunda llamada con mismo state
5. **Confirm gate** — sin `confirm=True` rechaza antes de tocar API

Total estimado: **20 tools × ≥3 tests = 60+ tests**. Igual que la suite gpon v2.2.0 (60 tests).

---

## 9. Edge cases a documentar al implementar

### Cloudflare
- `ttl=1` significa "automatic" (CF traduce a 300s). NO confundir con literal 1s.
- Records con mismo `name+type` distintos: válido en CF (round-robin DNS). Tools lo soportan.
- Rate limit: 1200 req/5min por token. Backoff implícito en cliente.
- Zone vs Account scope: el plugin trabaja con UN zone_id (casaredes.cc). Multi-zone fuera de scope.

### Pi-hole
- SID expira a 1h sin tocar. Si caller hace 2 llamadas separadas por > 1h, el client re-loguea silenciosamente.
- Pi-hole v6 reorganizó endpoints (de `/admin/api.php` a `/api/...`). El client usa solo v6 paths.
- Reload puede tardar 5-30s en hosts cargados (4M+ entries en gravity). Timeout 60s.
- `custom_dns` en /etc/pihole/dnsmasq.d/02-pihole-custom.conf (file fallback si API rota — fuera del scope, pero documentar).

### AdGuard
- Login no necesario (Basic Auth en cada request).
- `set_rewrites` con array vacío: VACÍA todas las rewrites (peligroso). El cliente exige `confirm=True` AND `len(rewrites) > 0` salvo que se pase param explícito `allow_empty=True`.
- AdGuard no expone idempotency keys — los `add`/`remove` helpers hacen read-modify-write, race conditions posibles si 2 callers concurrentes. Docs: aceptar last-write-wins.

---

## 10. Ejemplo de uso end-to-end

Caso real: añadir un nuevo subdominio `kb.casaredes.cc` apuntando al VPS.

```python
# 1. Listar custom DNS actual en TS L1 (debug previo)
pihole_list_custom_dns("pihole-tsl1", domain_filter="kb")
# → {ok: true, data: {records: [], count: 0}}

# 2. Crear record en Cloudflare (resolución pública, sin proxy)
cloudflare_dns_create_record(
    name="kb.casaredes.cc",
    type="A",
    content="100.69.126.35",      # VPS Hetzner
    ttl=1,
    proxied=False,                  # forzado por validation
    comment="kb wiki — 2026-05-10",
    confirm=True,
)
# → {ok: true, data: {id: "...", action: "created"}}

# 3. Añadir custom DNS en cada Pi-hole (LAN-only override)
for host in ["pihole-tsl1", "pihole-vps", "pihole-mun"]:
    pihole_add_custom_dns(
        host_ref=host,
        domain="kb.casaredes.cc",
        ip="10.0.1.40",            # nginx interno
        upsert=True,
        confirm=True,
    )

# 4. Reload los 3
for host in ["pihole-tsl1", "pihole-vps", "pihole-mun"]:
    pihole_reload(host_ref=host, component="dns", confirm=True)

# 5. Verify split-DNS funciona
# (manual test o future tool: pihole_query_log_search filtrando por kb.casaredes.cc)
```

Sin estas tools: el mismo flujo es ~12 curl calls + manejo de SID + JSON parse.

---

## 11. Roadmap dentro del plugin

| Versión | Scope |
|---------|-------|
| **v0.1.0** | Lo de este spec. 20 tools (6 CF + 8 Pi-hole + 6 AdGuard). |
| v0.2.0 | Pi-hole groups/clients/adlists CRUD. AdGuard parental + safe_browsing. |
| v0.3.0 | Cloudflare WAF rules (read-only). |
| v0.4.0 | Workflows compuestos: `add_subdomain(name, ip)` que orquesta CF + 3 Pi-hole + reload. |

---

## 12. Referencias

- [Cloudflare API v4 Docs — DNS Records](https://developers.cloudflare.com/api/operations/dns-records-for-a-zone-list-dns-records)
- [Pi-hole REST API v6 OpenAPI](https://docs.pi-hole.net/api/)
- [AdGuard Home REST API](https://github.com/AdguardTeam/AdGuardHome/blob/master/openapi/openapi.yaml)
- Gap doc fuente: `docs/operator-notes/tool-gaps-reverse-proxy-plan-20260510.md`
- Plugin pattern referencia: `plugins/nginx-ui-ops/` (mutation gating + Pydantic returns)
- Plugin pattern referencia: `plugins/gpon/` (multi-instance via inventory)
