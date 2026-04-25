# Contrato de plugin

🇬🇧 [Read in English](../plugin-contract.md)

Cada plugin vive en su propio directorio bajo `plugins/<nombre>/` y
expone un manifest llamado `plugin.toml`. El router recorre
`plugins/` al arrancar, parsea cada manifest y evalúa los
requirements de cada plugin contra el inventory declarativo. Los
plugins no se cargan si su manifest no parsea; no se activan si sus
requirements no se cumplen y el perfil activo no los permite.

## Layout del directorio

```
plugins/
  myplugin/
    plugin.toml         # obligatorio
    server.py           # [runtime].entry — la implementación del plugin
    requirements.txt    # opcional; referenciado vía [runtime].deps
```

Cualquier cosa que empiece por `.` o `_` se salta (se usa para
ejemplos y scaffolding que no debe activarse).

## Secciones del manifest

### `[plugin]` — identidad (obligatorio)

```toml
[plugin]
name    = "proxmox"       # obligatorio. Es el id del plugin en todas partes
version = "1.0.0"         # obligatorio
enabled = true            # opcional, default true
```

`enabled = false` mantiene el manifest visible en `router_status()`
pero el plugin nunca se activa — útil cuando quieres tener el
scaffolding presente pero en stand-by.

### `[runtime]` — cómo arranca el plugin

```toml
[runtime]
entry  = "server.py"          # fichero que el router debe importar
python = ">=3.11"             # informativo; comprobado contra el host
deps   = ["proxmoxer>=2.0"]   # deps de terceros; se resuelven vía uv/pip
venv   = "auto"               # "auto" | "shared" | ruta específica
```

Esto es informativo por ahora — el mount real por subprocess/import
está programado para una fase posterior. Los campos se parsean y
preservan para que el contrato sea estable para los autores de
plugin desde hoy.

### `[security]` — capacidades declaradas (obligatorio bajo `strict_manifest`)

```toml
[security]
inventory_access  = ["hosts:type=proxmox", "credentials:PROXMOX_*_TOKEN"]
credential_refs   = ["PROXMOX_*_TOKEN"]   # patrones glob que el plugin puede leer
network_dynamic   = true                  # puede abrir sockets TCP/UDP arbitrarios
filesystem_read   = []                    # rutas permitidas para lectura
filesystem_write  = []                    # rutas permitidas para escritura
exec              = []                    # comandos subprocess permitidos
```

`strict_manifest = true` en `config/router.toml` (el default)
significa que la ausencia de la tabla `[security]` cuarentena el
plugin. `credential_refs` es la fuente de verdad para
`router_add_credential`: el usuario solo puede guardar una
credencial cuya ref case con al menos un patrón de algún plugin
cargado.

### `[requires]` — lo que el plugin necesita antes de activarse

```toml
[[requires.hosts]]
type   = "proxmox"        # casa contra Host.type en el inventory
tag    = "prod"           # opcional; casa contra Host.tags
min    = 1                # default 1
prompt = "Necesito un nodo Proxmox con token API"

[[requires.credentials]]
pattern = "PROXMOX_*_TOKEN"   # patrón fnmatch contra env o vault
prompt  = "Token API con VM.Audit + VM.PowerMgmt"
```

Un plugin con uno o más requirements no satisfechos pasa a
`pending_setup`. El router entonces expone una tool dinámica
`setup_<plugin>()` cuya salida incluye cada `prompt` literal, para
que un LLM pueda guiar al usuario por los inputs concretos que
faltan.

### `[tools]` — allowlist/denylist opcional

```toml
[tools]
whitelist = ["*"]              # vacío/ausente = permitir todo
blacklist = ["destroy_*"]      # se comprueba primero; siempre niega
```

El matching usa `fnmatch` (case-sensitive). Helper:
`core.loader.tool_allowed`. Se aplica en mount-time cuando el
router carga las tools del plugin; el helper está hoy y la
aplicación se activa cuando el mount por subprocess/import esté.

## Estados del ciclo de vida

| Estado | Significado |
|--------|-------------|
| `ok` | Requirements cumplidos; tools disponibles. |
| `pending_setup` | Manifest cargado pero `[requires]` no cumplido. `setup_<plugin>()` está expuesto. |
| `disabled` | El manifest dice `enabled = false`. |
| `disabled_by_profile` | La allowlist `enabled_plugins` del perfil activo excluye el plugin. |
| `error` | El plugin falló al arrancar tras llegar a `ok` (errores en runtime). |
| `quarantined` | El `plugin.toml` no parseó — visible en `LoadReport.quarantined`. |

TOML malformado ya no aborta discovery: los hermanos siguen
cargando. El directorio roto queda registrado para que el usuario
vea qué falla sin que el router se niegue a arrancar.

## Detección de diff

`config/.last_state.json` almacena el conjunto de nombres de plugin
que se activaron en la ejecución previa. En cada `reconcile()` el
router reporta `added`, `removed` y `unchanged`. Soltar un nuevo
directorio de plugin o borrar uno existente es visible en el
siguiente arranque — sin flag de restart.
