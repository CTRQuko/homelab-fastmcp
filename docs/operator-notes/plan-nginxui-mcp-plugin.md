# Plan — Integrar nginx-ui MCP como plugin de mimir

**Estado**: borrador inicial. NO implementar — esperar decisión del
operador entre las dos opciones de integración.
**Fecha**: 2026-05-06.
**Ámbito**: framework `mimir-mcp` y/o un nuevo plugin `nginxui`.

---

## Context

`nginx-ui` (https://nginxui.com) es un panel web open-source para
gestionar nginx. La versión moderna **incluye un servidor MCP integrado**
en el propio daemon, expuesto en `http://<host>:9000/mcp` con
autenticación por query param `node_secret`. Permite a un LLM:

- **Recursos** (read-only): estado de nginx, configs activas.
- **Tools** (ejecutables): restart/reload nginx, modificar configs.

El operador ya tiene nginx-ui corriendo en LXC 104 (`srv2-lxc-nginx-104`,
`10.0.1.40`, ya identificado en sesiones previas como reverse-proxy
catch-all del homelab). Quiere poder usar las tools de nginx-ui MCP a
través de mimir, junto con los plugins existentes (homelab, gpon, uart),
sin tener que cambiar de cliente MCP.

---

## Estado actual de nginx-ui MCP (datos verificados de la doc oficial)

| Aspecto | Valor |
|---------|-------|
| Tipo | Daemon HTTP integrado en el binario de nginx-ui |
| Transporte | **SSE** (Server-Sent Events) sobre HTTP |
| Endpoint | `http://<nginxui-host>:9000/mcp` |
| Auth | Query param `?node_secret=<secret>` |
| Tools expuestas | restart, reload, edit config, resources read-only (lista exacta no documentada en la guide; hay que descubrirla via `tools/list` en runtime) |
| Standalone binary | NO — el MCP es un módulo del daemon nginx-ui mismo |

**No es invocable como subprocess** — no hay `nginxui-mcp` standalone.
Es un endpoint HTTP del daemon que ya está corriendo.

---

## Restricción del framework mimir

`mimir-mcp/router.py:_plugin_mount_config` (líneas 633-705) genera el
config para `fastmcp.create_proxy` con dos shapes soportadas hoy:

```toml
# Forma 1: command + args (subprocess)
[runtime]
command = "uv"
args = ["run", "my-plugin"]

# Forma 2: entry (Python script)
[runtime]
entry = "server.py"
```

**Ninguna soporta `url` / `transport: sse`** directo. Si pongo un manifest
`[runtime].url = "http://..."`, mimir lo ignorará o explotará al pasar
el config a FastMCP esperando un `command`.

Hay dos caminos para integrar nginx-ui:

---

## Opción A — Bridge subprocess (sin tocar el framework)

Crear un plugin `nginxui-bridge` que es subprocess Python stdio. Por
dentro, actúa de proxy MCP: recibe llamadas vía stdio (lo que mimir
sabe orquestar) y las reenvía al daemon nginx-ui vía SSE.

### Coste

~80-150 LOC. FastMCP `Client` ya sabe hablar SSE (`fastmcp.Client(url="...")`).
La pieza mínima sería:

```python
# plugins/nginxui/server.py (esqueleto)
import asyncio, os
from fastmcp import FastMCP, Client

upstream_url = (
    f"http://{os.environ['NGINXUI_HOST']}:{os.environ.get('NGINXUI_PORT','9000')}"
    f"/mcp?node_secret={os.environ['NGINXUI_NODE_SECRET']}"
)

# FastMCP Server que hace forwarding al daemon SSE.
mcp = FastMCP("nginxui-bridge")

async def main():
    async with Client(upstream_url) as client:
        # Auto-mount: descubre tools/resources del upstream y los expone
        # como propias del bridge (FastMCP soporta proxy directo).
        proxy = mcp.create_proxy(client)
        await proxy.run_stdio_async()

if __name__ == "__main__":
    asyncio.run(main())
```

### Plugin manifest

```toml
[plugin]
name = "nginxui"
version = "0.1.0"
enabled = true

[runtime]
command = "uv"
args = ["run", "--with", "fastmcp", "python", "{plugin_dir}/server.py"]

[security]
credential_refs = ["NGINXUI_HOST", "NGINXUI_PORT", "NGINXUI_NODE_SECRET"]
```

### Ventajas
- **Cero cambios al framework**. Plugin self-contained.
- Patrón replicable: cualquier MCP-via-HTTP/SSE futuro se monta igual.
- Operador puede deshabilitar el plugin solo si nginx-ui daemon cae.

### Desventajas
- Capa adicional de subprocess (latencia ~10-50ms extra por call).
- Cada plugin "remoto" reinventa el bridge.
- Si tools del daemon cambian, hay que reiniciar el bridge subprocess.

---

## Opción B — Extender mimir framework con `[runtime].url`

Añadir soporte nativo en `_plugin_mount_config` para una tercera
shape:

```toml
[plugin]
name = "nginxui"
version = "0.1.0"

[runtime]
url = "http://{NGINXUI_HOST}:{NGINXUI_PORT}/mcp"
url_query = { node_secret = "{NGINXUI_NODE_SECRET}" }
transport = "sse"  # explicit, default "stdio"

[security]
credential_refs = ["NGINXUI_HOST", "NGINXUI_PORT", "NGINXUI_NODE_SECRET"]
```

`_plugin_mount_config` traduce a FastMCP config:

```python
{
    "mcpServers": {
        "default": {
            "url": "http://10.0.1.40:9000/mcp?node_secret=...",
            "transport": "sse",
        }
    }
}
```

(Verificar en FastMCP source si `mcpServers.<name>.url` está soportado;
si no, `Client(url, transport="sse")` envuelto en `create_proxy` se hace
inline.)

### Coste

- ~40 LOC en `_plugin_mount_config` (nuevo if antes de los dos shapes
  existentes).
- ~20 LOC en validación de manifest (nuevo campo `url`).
- ~6-8 tests en `test_router_wiring.py` (mount con url, error si falta
  url+command+entry).
- 0 LOC en plugin: el manifest ES el plugin completo.
- Doc en framework-deferrals.md: "remote MCP plugins via SSE/HTTP".

### Ventajas
- **Una sola línea de plugin**: solo manifest, cero código.
- Reutilizable para cualquier MCP HTTP/SSE futuro (paperless-ngx,
  immich, etc., si en algún momento exponen MCP).
- No subprocess intermedio = menos latencia + menos memoria.
- Substitución de `{ENV_VAR}` en URL/query es feature limpia que
  encaja con el patrón existente `{plugin_dir}`.

### Desventajas
- Toca el core del framework (require PR + tests + bump versión).
- Necesita validar que FastMCP `create_proxy` admite `url` directo en
  `mcpServers.<name>` (probable que sí — la spec MCP cubre transports
  http/sse — pero hay que confirmar antes de commit).
- Plugin upstream `nginxui` se queda más simple pero la complejidad
  migra al framework.

---

## Recomendación

**Opción B** si el framework va a tener más plugins remotos en el
futuro (probable: hay un ecosistema creciente de MCP servers HTTP).

**Opción A** si quieres aterrizarlo YA y el framework aún no tiene
otros casos de uso remotos pendientes.

**Camino mixto realista**:
1. **Hoy: Opción A** (plugin bridge) — yo lo monto en ~30 min, valida
   end-to-end con tu nginx-ui, descubrimos qué tools devuelve, etc.
2. **Después (cuando aparezca un segundo MCP HTTP que integrar)**:
   migrar a Opción B y eliminar el plugin bridge.

Eso evita over-engineering ahora pero deja la puerta abierta a la
solución "correcta" cuando haya 2+ MCP HTTP remotos justificándola.

---

## Open questions (necesito que confirmes)

1. **Dónde corre nginx-ui MCP**: ¿LXC 104 (`10.0.1.40:9000`) o lo has
   movido? ¿Está expuesto el puerto 9000 en LAN o solo localhost del LXC?
2. **`node_secret`**: ¿dónde lo obtengo? ¿Está en la web admin de
   nginx-ui? ¿Lo guardas en `secrets/` o lo genero ahora?
3. **Scope**: ¿quieres acceso completo (tools que reload/restart/edit
   configs) o restringir a read-only (solo `tools/list` + resources)
   en el primer paso? El framework de mimir tiene whitelist/blacklist
   por tool — lo aprovechamos.
4. **Persistencia**: ¿guardas `NGINXUI_NODE_SECRET` en
   `C:\homelab\.config\secrets\nginxui.md` con la convención que ya
   tienes (apispve.md, tailscale.md, etc.) y lo cargamos via
   `credential_refs` del manifest?
5. **Opción A vs B**: ¿prefieres el bridge rápido ahora (Opción A) o
   inversion en el framework de una vez (Opción B)?

---

## Implementation outline (cuando elijas opción)

### Si Opción A (bridge plugin)

```
Step 1: Crear plugins/nginxui/ con manifest + server.py bridge.
        (~30 min)
Step 2: Tests del bridge con mock SSE.
        (~30 min)
Step 3: Smoke test real contra el daemon nginx-ui en LXC 104:
        - tools/list → ver qué tools expone realmente
        - llamar nginx_status (read-only) → verificar response
        (~10 min)
Step 4: Whitelist tools si solo quieres read-only en primera iteración.
        (~5 min)
Step 5: Commit + push (rama propia, no main).
        (~5 min)
```

Total: ~80 min.

### Si Opción B (framework extension)

```
Pre-step: Verificar que FastMCP create_proxy admite mcpServers.url
          (lectura de fastmcp source o test pequeño).
          (~15 min)
Step 1:   Modificar core/loader.py + router.py:_plugin_mount_config
          para soportar [runtime].url + transport.
          (~45 min)
Step 2:   Añadir substitución {ENV_VAR} en URL/query.
          (~20 min)
Step 3:   Tests en test_router_wiring.py + test_core_loader.py.
          (~45 min)
Step 4:   plugins/nginxui/plugin.toml minimalista (sin código).
          (~10 min)
Step 5:   Smoke test real + whitelist tools si procede.
          (~15 min)
Step 6:   Bump mimir framework versión + CHANGELOG.
          (~10 min)
Step 7:   Commit + push.
```

Total: ~2.5h.

---

## Verification end-to-end (post-implementación)

1. `mimir router --dry-run` lista el plugin nginxui como `ok` (con
   credenciales setadas) o `pending_setup` (sin ellas).
2. Llamar `mimir-mcp_nginxui_<tool>` desde Claude Code → respuesta.
3. Si el plugin trae tool `nginx_status` (o equivalente):
   resultado refleja el estado real del nginx en LXC 104.
4. Restart nginx test (CON cuidado, en horario tranquilo): tool
   `nginx_restart` → service vuelve UP en <5s.
5. Audit log: cada call queda registrado con `client=` y
   `error_message=` (heredado de v0.5.0 audit enrichment).
6. Whitelist test: si pongo `[tools].whitelist = ["nginx_status"]`,
   solo esa tool aparece — restart/reload no listadas.

---

## Riesgos / mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| `node_secret` leakeado en URL del subprocess | Subprocess env vars no se ven en logs externos. URL no se loguea por mimir. Audit log NO incluye URLs (solo args_hash). |
| Tools de nginx-ui mutaciones (restart) ejecutadas por error | Whitelist en mimir; primera iteración solo read-only. |
| nginx-ui daemon caído → bridge subprocess en bucle reconnect | FastMCP Client tiene timeouts. Si daemon up=False, bridge falla loud al startup; mimir lo marca pending. |
| FastMCP create_proxy no soporta SSE bien (Opción B) | Verificación previa en pre-step. Si falla, fallback a Opción A. |
| Versión nginx-ui MCP cambia tools sin aviso | El bridge re-lista tools cada vez que arranca; cambio rompe llamadas pero NO el bridge mismo. Ver runtime-issues.md. |

---

## Decisiones cerradas (irán aquí cuando respondas)

(vacío hasta que decidas)
