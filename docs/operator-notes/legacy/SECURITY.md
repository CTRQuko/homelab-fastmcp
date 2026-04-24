# Modelo de seguridad

## Amenaza / no-amenaza

**Modelo:** homelab personal, un usuario, máquina local. Cliente MCP local
(Claude Desktop, OpenCode) comunicándose vía stdio.

**No somos un servicio expuesto a Internet.** Por eso no implementamos
autenticación MCP, rate limiting local, ni cifrado en reposo.

## Secrets

### Dónde viven

Prioridad de carga (implementada en `native_tools/secrets.py`):

1. **Environment variable** — para inyección en runtime (CI, MCP config)
2. **`$HOMELAB_DIR/.config/secrets/*.md`** — ficheros por servicio fuera del repo
3. **`.env`** en la raíz del proyecto — solo desarrollo, **en `.gitignore`**

**Nunca** se hardcodean en código fuente. Verificación con `grep` periódica.

### Qué secrets se usan

| Variable | Uso | Rotación recomendada |
|---|---|---|
| `UNIFI_API_KEY` | Autenticación REST contra controlador UniFi | al cambiar de red |
| `TAILSCALE_API_KEY` | Autenticación API Tailscale | 90 días |
| `GITHUB_TOKEN` | Rate limit 5000 req/h vs 60 anonymous | cuando expire |
| `GPON_PASS` | SSH al stick GPON | al cambiar hardware |

### Si un secret se expone

Checklist:

1. **Revocar** la key/token en el panel correspondiente
2. **Generar** nueva key
3. **Actualizar** en el archivo donde viva (`secrets/*.md` o env)
4. **Reiniciar** el aggregator
5. **Git log** — comprobar si llegó a commitearse. Si sí, `git-filter-repo` o
   considerar el secret comprometido permanentemente

## Validación de inputs

Todas las tools nativas validan sus parámetros antes de hacer I/O:

| Input | Regex | Limite |
|---|---|---|
| `device_id` (Tailscale) | `^[a-zA-Z0-9_-]{1,64}$` | 64 chars (defensa DoS) |
| `owner`/`repo` (GitHub) | `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` | empieza por alfanumérico |
| `state` (GitHub) | `^(open\|closed\|all)$` | enum |
| `issue_number` (GitHub) | `int >= 1` | positivo |
| `baudrate` (UART) | int válido pyserial | rangos estándar |

Las validaciones fallan con `ValueError` **antes** de llegar a la red o al
hardware.

## Sanitización de errores

`native_tools/tailscale._sanitize_error()` detecta 9 patrones de credenciales
en mensajes de excepción:

- `tskey-api-*`, `tskey-oauth-*`, `tskey-client-*`
- `api_key*`, `apikey*`, `api-secret*`
- `bearer <valor>`, `token=<valor>`, `authorization: <valor>`

Si detecta cualquiera, **sustituye el mensaje completo** por
`"tailscale API error (credenciales ocultas)"`. Esto protege contra fugas
en logs de cliente MCP, Engram, o pantallas de LLM.

**Cobertura:** los 6 endpoints de Tailscale aplican `_sanitize_error` en sus
`except`. Verificado por `test_critical.py::test_tailscale_endpoint_sanitizes_*`.

## Logging seguro

- Log a **stderr** (stdout reservado al protocolo MCP)
- `mask()` en `secrets.py` permite mostrar primeros 4 chars + `****`
- **Nunca** se loggean valores de secrets cargados
- Error messages no incluyen valores de otras keys cargadas

## Lo que NO protegemos

Listado explícito para dejar claro el perímetro:

- **Acceso físico al disco**: `.env` en claro. Cifrado en reposo no aplica.
- **Autenticación MCP**: cualquier proceso con stdin/stdout tiene acceso.
  Esto es diseño — el cliente MCP lo ejecuta como subprocess.
- **Rate limiting local**: un cliente malicioso puede spammear. No relevante
  en homelab personal.
- **Audit log de operaciones**: `delete_device`, `create_issue` no dejan rastro
  local. El cliente MCP sí loggea qué tools llama.
- **Validación de salida**: asumimos que los downstreams MCP devuelven datos
  confiables (son procesos locales bajo nuestro control).

## Auditoría

| Fecha | Alcance | Resultado |
|---|---|---|
| 2026-04-21 | `native_tools/gpon_native.py` con creds hardcoded | ✅ Eliminado |
| 2026-04-21 | `conversacion.md` con 3 API keys en claro | ✅ Eliminado (nunca commiteado) |
| 2026-04-21 | UniFi API key expirada causaba 401 | ✅ Rotada |
| 2026-04-22 | 6 bugs funcionales detectados vía TDD | ✅ Corregidos |
| 2026-04-22 | Regex `_DEVICE_ID_RE` sin límite longitud | ✅ `{1,64}` |
| 2026-04-22 | `github._client()` degradación silenciosa | ✅ `UserWarning` |
| 2026-04-22 | `_validate_name` aceptaba `.hidden`, `-start` | ✅ Regex más estricto |

Ver `docs/CHANGELOG.md` para el detalle completo.
