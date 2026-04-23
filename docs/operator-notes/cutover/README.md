# Fase 7 + Fase 8 — Cutover Plan

Este directorio contiene todo lo que hace falta para mover los clientes
MCP (Hermes LXC 302 pve2 + Claude Desktop) desde `server.py` legacy al
nuevo `router.py`, sin tocar nada en producción hasta que el operador dé
el OK explícito.

Los ficheros `manifests/<nombre>/plugin.toml` son manifests reales
parseables por el loader del router y están cubiertos por un test
(`tests/test_cutover_manifests.py`) para que cualquier cambio se note en
CI antes de ejecutar el cutover.

## Mapa de plugins

Nombre       | Origen actual                                   | Target repo (ya existe)                | Propagation de env
-------------|-------------------------------------------------|----------------------------------------|--------------------------
`proxmox`    | `mcp-servers/homelab-mcp/homelab_mcp/proxmox_mcp` | `CTRQuko/homelab-mcp` (compartido)     | `PROXMOX_*`
`linux`      | `mcp-servers/homelab-mcp/homelab_mcp/linux_mcp`   | `CTRQuko/homelab-mcp` (compartido)     | —
`windows`    | `mcp-servers/homelab-mcp/homelab_mcp/windows_mcp` | `CTRQuko/homelab-mcp` (compartido)     | —
`docker`     | `mcp-servers/homelab-mcp/homelab_mcp/docker_mcp`  | `CTRQuko/homelab-mcp` (compartido)     | —
`unifi`      | `uvx unifi-mcp-server` (upstream)               | paquete pip/uvx ya publicado           | `UNIFI_*`
`uart`       | `mcp-servers/mcp-uart-serial/`                  | `CTRQuko/serial-mcp-toolkit`            | —
`gpon`       | `mcp-servers/gpon-mcp/`                         | `CTRQuko/gpon-mcp`                      | `GPON_*`

### Native tools (deferidos — NO parte de Fase 7 estricta)

`native_tools/github.py`, `native_tools/tailscale.py` y
`native_tools/uart_detect.py` son módulos Python en-proceso, no servers
MCP. Opciones para Fase 7c (no bloquea cutover):

- **Mantener como first-party**: migrarlos a `core/` del framework y
  registrarlos vía `@mcp.tool()` directamente en `router.py`. No
  spawnean subprocess, no necesitan `plugin.toml`.
- **Envolverlos en un micro-MCP**: crear `plugins-native-mcp` con
  entrypoint que expone los tools via FastMCP. Más coherente con el
  modelo "todo es plugin", más boilerplate.

La decisión se toma después del cutover básico — hoy son 3 módulos que
pesan poco y funcionan.

## Fase 7b — Ejecución (requiere OK del operador)

Todos los repos target ya existen, así que no hace falta `gh repo
create`. La extracción se reduce a **añadir `plugin.toml`** a cada repo
externo y **checkoutear** desde el framework.

### Paso 1 — Añadir plugin.toml a cada repo externo

Para cada fila de la tabla:

```bash
# Ejemplo con homelab-mcp (proxmox + linux + windows + docker comparten repo)
cd C:/homelab/mcp-servers/homelab-mcp
cp C:/homelab/mcp-servers/homelab-fastmcp/docs/cutover/manifests/proxmox/plugin.toml .
git add plugin.toml
git commit -m "chore: add plugin.toml for homelab-fastmcp router integration"
git push
```

> **Gotcha:** `CTRQuko/homelab-mcp` agrupa 4 plugins (proxmox + linux +
> windows + docker). Dos opciones:
>
> - **A.** El repo lleva 4 `plugin-*.toml` y el framework acepta los 4
>   como "plugins hermanos en el mismo checkout". Requiere extender el
>   loader para leer todos los `plugin-*.toml` del dir. **No está hecho
>   hoy.** Recomendado solo si se acepta el cambio de loader.
> - **B.** Repartir el repo en 4 (o dejar proxmox como único plugin
>   "homelab-mcp" que expone los 4 sub-MCPs via mount interno). Más
>   trabajo pero limpio conceptualmente.
>
> Para MVP sugiero **variante C**: añadir un solo `plugin.toml` a
> `homelab-mcp` con nombre `homelab` que ejecuta el launcher que hoy
> vive en `server.legacy.py` — monta proxmox+linux+windows+docker como
> sub-proxies de ese plugin. Minimiza cambios. Se trocea después.

### Paso 2 — Checkout en el framework

```bash
cd C:/homelab/mcp-servers/homelab-fastmcp

# Cada plugin es un symlink o clon fresco en plugins/
# (elige uno — en Windows los symlinks requieren admin, en Linux/WSL no)
mkdir -p plugins

# Variante symlink (Windows: elevar PowerShell)
cmd /c mklink /D plugins\proxmox C:\homelab\mcp-servers\homelab-mcp

# Variante clone (duplica código pero no requiere admin)
git clone https://github.com/CTRQuko/homelab-mcp plugins/proxmox
```

### Paso 3 — Arrancar el router en modo real y validar

```bash
.venv/Scripts/python.exe router.py --dry-run
# Comprueba que cada plugin aparece con status=ok o pending_setup con
# los missing correctos.

.venv/Scripts/python.exe router.py
# Deja corriendo. Usa mcp-inspector o un cliente de prueba para
# verificar que ve proxmox_list_nodes, linux_run_command, etc.
```

## Fase 8 — Cutover de clientes

### Pre-check (obligatorio antes de tocar config)

1. `router.py --dry-run` en la máquina del cliente muestra **todos** los
   plugins OK (o pending_setup aceptable).
2. Compara el set de tools que expone `router.py` contra el que
   `server.legacy.py` expone hoy. Usa `tests/test_integration.py` o un
   script que liste `await mcp.list_tools()` en ambos.
3. Ten abierta la config actual del cliente en un editor antes de
   modificarla — el rollback es "pegar la versión antigua".

### Hermes LXC 302 pve2

Ruta dentro del LXC: `/root/.config/<claude-client>/config.json` o donde
Hermes lea su config MCP (depende del agente). El patch típico:

```diff
 "mcpServers": {
   "homelab-fastmcp": {
     "command": "uv",
     "args": [
       "--directory",
       "/root/homelab/mcp-servers/homelab-fastmcp",
-      "run",
-      "python",
-      "server.py"
+      "run",
+      "python",
+      "router.py"
     ]
   }
 }
```

Reinicia el agente. Primera prueba: `router_status()` o `router_help()`
— ambos son tools propios del router nuevo, no existen en el legacy, así
que si aparecen el cutover está vivo.

### Claude Desktop (máquina Windows de Jandro)

Ruta: `%APPDATA%\Claude\claude_desktop_config.json`. Mismo patch que
Hermes adaptado a rutas Windows:

```diff
 "homelab-fastmcp": {
   "command": "uv",
   "args": [
     "--directory",
     "C:/homelab/mcp-servers/homelab-fastmcp",
-    "run", "python", "server.py"
+    "run", "python", "router.py"
   ]
 }
```

Reinicia Claude Desktop. Valida igual que Hermes.

### Rollback

Un único cambio por cliente. Para revertir: pega la versión previa del
JSON y reinicia el cliente. Toma segundos. No hay estado persistente
que migrar.

## Fase 8b — Limpieza (una vez Hermes + Claude Desktop llevan ≥1 semana OK)

```bash
cd C:/homelab/mcp-servers/homelab-fastmcp
git rm server.legacy.py
git rm -r native_tools/   # si ya migraste github/tailscale/uart_detect
# NO borres mcp-servers/homelab-mcp/, gpon-mcp/, mcp-uart-serial/ — son
# repos separados que siguen siendo los "upstream" de los plugins.
```

Merge `refactor/modular-framework` → `main`, push, tag release.

## Verificación automática

El test `tests/test_cutover_manifests.py` carga cada `plugin.toml` de
`docs/cutover/manifests/` mediante `core.loader.parse_manifest` y
comprueba que:

- Los campos obligatorios (`plugin.name`, `plugin.version`, `[security]`)
  están presentes.
- `[runtime].command` o `[runtime].entry` está declarado (sin esto, el
  router no podría mountarlos).
- `_plugin_subprocess_env` no crashea con la declaración de `credential_refs`.

Si alguien edita los manifests y rompe el schema, CI grita.
