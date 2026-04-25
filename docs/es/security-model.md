# Modelo de seguridad

🇬🇧 [Read in English](../security-model.md)

El framework asume que los plugins son código no confiable. Hoy hay
cuatro capas de defensa; una quinta (interceptores en runtime) está
planeada para una fase posterior.

## Capa 1 — Validación del manifest

Cada `plugin.toml` se parsea al arrancar. Secciones ausentes o
malformadas mueven el plugin a **quarantine** en vez de tirar el
router. Bajo `strict_manifest = true` (el default), una sección
`[security]` es obligatoria.

Un plugin en quarantine es visible en `router_status()` y en el
log de arranque para que el usuario vea qué falló — pero **no
carga** y sus capacidades declaradas **no** ensanchan ninguna
allowlist. Soltar un `plugin.toml` malicioso en `plugins/` no le
da acceso a credenciales.

## Capa 2 — Audit log centralizado

`core.audit.log_tool_call()` añade una línea JSON por invocación
de tool a `config/audit.log`. Las entradas contienen timestamp,
plugin, nombre de tool, hash SHA-256 de los argumentos (nunca los
valores crudos), duración y status. Las escrituras son
fire-and-forget para que un fallo de audit no pueda bloquear una
llamada, y la rotación es diaria por fecha.

Cada tool que el router expone está envuelta: las meta-tools
`router_*`, cada `setup_<plugin>()`, y las tools de discovery
`skill_*`/`agent_*`. Los caminos de error registran
`status = "error:<TipoExcepción>"` para que las llamadas fallidas
aparezcan en el log en vez de desaparecer en silencio. Cuando
aterrice el mount de plugin (ver Capa 5), hereda el mismo
contrato — sus tools deben pasar por el helper de registro envuelto
en audit.

**Los secretos nunca se loguean.** `router_add_credential` omite
deliberadamente el campo `value` del dict de audit; solo `ref` se
hashea. Cualquier autor de plugin que copie este patrón debe hacer
lo mismo.

## Capa 3 — Vault de credenciales scoped

Las credenciales nunca viven en YAML plano y los plugins nunca las
leen desde `os.environ`. Se piden a través de
`core.secrets.get_credential(ref, plugin_ctx)`, que:

1. Verifica que el manifest del plugin declara un patrón
   `credential_refs` que casa con `ref`.
2. Busca el valor en este orden: env var → fichero del vault
   (`$MIMIR_HOME/secrets/router_vault.md`) → miss.
3. Registra el acceso en audit (solo el hash del ref).

Las escrituras pasan por `router_add_credential`, que:

- Rechaza refs que no casen `^[A-Z][A-Z0-9_]{2,63}$`.
- Rechaza valores con newline o NUL (previene la inyección de un
  segundo key/value que se escape del scope del llamador).
- Rechaza refs que no casen el `credential_refs` de ningún plugin
  cargado — no se vuelcan secretos arbitrarios al vault.
- Pone modo 0o600 en POSIX.

Plugins marcados `disabled` o `error` **no** contribuyen patrones
a la allowlist, así que un manifest deshabilitado no puede
ensanchar el scope de credenciales.

### Scoping para plugins en subprocess

Los plugins mountados via `create_proxy` corren como procesos
hijos, así que el check in-process de `get_credential` no llega a
ellos — leen env directamente. El router por tanto *construye* el
env de cada subprocess explícitamente
(`router._plugin_subprocess_env`):

- Las env vars normales del sistema (`PATH`, `APPDATA`, `HOME`,
  `PYTHON*`, `MIMIR_HOME`, etc.) pasan para que el intérprete hijo
  arranque.
- Las vars con forma de credencial (mayúsculas + underscore,
  longitud ≥ 3) solo se propagan cuando casan con los patrones
  `credential_refs` **de este plugin**, **o** no son reclamadas
  por ningún otro plugin cargado. Una var reclamada por el plugin
  B pero no por el plugin A se elimina del env del subprocess de A.
- Refs que solo viven en `secrets/*.md` o `.env` (no en
  `os.environ`) se resuelven via `core.secrets.resolve_refs_matching`
  y se mergean, así el subprocess ve la misma vista que devolvería
  la API del vault.

Efecto neto: plugins hermanos en subprocess no pueden ver los
tokens unos de otros aunque compartan el mismo proceso router.

## Capa 4 — Profile gate

`profiles/<name>.yaml` es una allowlist explícita de nombres de
plugin que pueden activarse. Esto corre **después** de la
evaluación de requirements, así que:

- Fichero vacío o sin clave `enabled_plugins` → no hay gate, todo
  plugin descubierto puede cargar.
- `enabled_plugins: []` → no carga ningún plugin; solo core +
  meta-tools visibles. Es el default en `profiles/default.yaml`.
- `enabled_plugins: [a, b]` → solo `a` y `b` se activan; los
  demás van a `disabled_by_profile`.

El gate es una segunda allowlist sobre el manifest — aunque los
requirements del plugin estén cumplidos, el profile puede
rechazarlo. Cambiar de profile es la forma más rápida de reducir
la superficie de tools expuestas sin editar plugins.

## Capa 5 — Interceptores en runtime

La aplicación se divide en dos porque las dos superficies tienen
perfiles de coste muy distintos:

- **`[tools].whitelist/blacklist`** — **aplicado.** Un único
  middleware FastMCP (`router._make_tool_filter_middleware`)
  consulta un dict de policy por namespace construido en tiempo de
  `build_mcp`. Corre en dos hooks:
  - `on_list_tools`: las tools denegadas se eliminan de la
    respuesta para que el LLM nunca las vea. Reduce superficie de
    tokens y de ataque.
  - `on_call_tool`: un cliente que llame a un nombre denegado
    igualmente (cache de list rancia, cliente malicioso) recibe un
    `ValueError` limpio en lugar de la tool ejecutándose. Defensa
    en profundidad.

  Los patrones del manifest casan contra el nombre **local** de la
  tool (con el prefijo de namespace stripped), así que
  `blacklist = ["destroy_vm"]` bloquea `<plugin>_destroy_vm`.
  Tools fuera del namespace de un plugin mountado (`router_*`,
  `skill_*`, meta-tools del core) siempre pasan — el filtro está
  scoped estrictamente a tools declaradas por plugin.
- **`network_dynamic`, `filesystem_read`, `filesystem_write`,
  `exec`** — todavía planeado. Requieren interceptar en las
  fronteras subprocess/socket/pathlib. En proceso son advisory en
  el mejor caso (cualquier plugin puede hacer monkeypatch para
  saltárselos); hechos correctamente requieren que el plugin corra
  en un proceso hijo controlado por el router. Programado junto al
  sandbox de runtime, no antes.

## Resumen del modelo de amenazas

| Amenaza | Capa | Mitigación |
|---------|------|-----------|
| `plugin.toml` malicioso jala credenciales arbitrarias | 1+3 | Quarantine en parse error; plugins disabled no ensanchan la allowlist de credenciales. |
| Plugin loguea un secreto por accidente | 2 | `router_add_credential` nunca loguea el valor; audit solo hashea. |
| Plugin lee `.env` directamente | 3 | Credenciales resueltas via `core.secrets` con check de scope; plugins nunca ven env crudo. |
| Inyección newline en credencial escapa el scope | 3 | Caracteres de control rechazados al escribir. |
| Usuario expone más tools de las que quería | 4 | Allowlist `profiles/<name>.yaml`; profile vacío = solo core. |
| Plugin expone una tool que el operador no quiere invocable | 5 | `[tools].whitelist/blacklist` aplicado por middleware FastMCP en list + call. |
| Plugin quiere filesystem fuera del `filesystem_read` declarado | 5 | Planeado. Requiere frontera de proceso — programado con sandbox de runtime. |
| Plugin exfiltra datos vía sockets arbitrarios | 5 | Planeado. Misma fase que el anterior. |

## Limitaciones conocidas

Dos restricciones son inherentes al transport MCP stdio y no
pueden arreglarse del todo dentro del router. Quedan flagged aquí
para que autores de plugin y operadores puedan planificar
alrededor.

### Las tools `setup_<plugin>()` persisten toda la sesión

MCP stdio no tiene forma de que un servidor des-registre una tool
tras el handshake. Una vez `setup_<plugin>()` se expone al
arrancar (porque el plugin estaba en `pending_setup`), el cliente
la sigue viendo el resto de la sesión aunque el plugin alcance
`ok`.

El router lo mitiga leyendo el estado *vivo* dentro de la tool:
un plugin completado devuelve `{"status": "ok", "missing": []}`
para que el LLM vea que el setup ya está. La tool en sí queda
expuesta, pero ya no miente. Usa `router_status()` como única
fuente de verdad para el set de plugins activos; trata las tools
`setup_*` como atajos para un solo plugin, no como señal fiable
de "pendiente".

### Los valores de credenciales pasan por el cliente MCP

Cuando un LLM llama `router_add_credential(ref, value)`, el
argumento `value` se serializa por el cliente MCP antes de llegar
al router. El audit log del router nunca escribe el valor (solo
un hash del `ref`), pero cualquier transcript o log de tool-call
del lado cliente registra el argumento tal cual. Para la mayoría
de los clientes MCP esto significa que la credencial cruda quedará
en el historial de conversación.

Si eso es inaceptable, añade las credenciales out-of-band: o
escribiéndolas directamente en `$MIMIR_HOME/secrets/router_vault.md`
(mismo formato `ref = value`, una por línea, modo `0o600`) o
seteando la env var correspondiente antes de arrancar el router.
El orden de lookup de `get_credential()` es env var → fichero del
vault, así que cualquiera de los dos caminos funciona sin que el
LLM vea jamás el valor.

## No-objetivos

- **Protección frente a sandbox escape** — el framework no es un
  sandbox de seguridad. Código malicioso decidido corriendo en el
  proceso router puede saltarse cualquier check a nivel Python.
  Las capas de arriba elevan el coste del daño accidental y
  estrechan el scope de los plugins confiables; no detienen a un
  plugin hostil que el usuario instala y activa a propósito.
- **Rotación de credenciales** — el vault es un almacén, no un
  gestor de rotación.
- **Audit de lecturas fuera de tools MCP** — solo se auditan
  invocaciones de tools; el interior de los plugins no está
  instrumentado.
