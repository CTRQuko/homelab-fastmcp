# Guía de nomenclatura

🇬🇧 [Read in English](../naming-guide.md)

Documento canónico para entender qué pieza es qué en este framework
y cómo nombrar las tuyas sin colisionar. Léelo antes de empezar un
plugin, sobre todo si piensas publicarlo.

Este es el contrato público: si viene otra persona a escribir un
plugin, esperamos que siga estas convenciones. El código las exige
donde puede (regex de `plugin.name`, por ejemplo) y las sugiere
donde no (nombre del repo de un plugin terceiro).

## 1. Roles — qué es qué

### El framework

Un único paquete Python (nombre canónico: `mimir-mcp`) que expone
un `router.py` como entrypoint MCP. Se puede instalar solo, sin
plugins, y sigue siendo útil: provee meta-tools para que el LLM
añada hosts, servicios y credenciales al `inventory/` del operador.

No asume infraestructura alguna. No trae plugins de serie. No
hardcodea hosts, IPs ni credenciales en el código.

### Core modules (`core/`)

Son los módulos internos del framework: `secrets`, `audit`,
`inventory`, `memory`, `loader`, `profile`, `skills`. Ninguno
expone tools MCP directamente. Son la maquinaria que consumen el
router y los plugins.

Un plugin **no** importa `core/*` directamente — interactúa con el
framework vía `plugin.toml` y env vars que el router inyecta. Este
aislamiento es lo que permite publicar plugins fuera del repo del
framework.

### Meta-tools del router

Las únicas tools que el framework expone al LLM por defecto,
definidas en `router.py`. Empiezan por `router_` y son parte del
contrato — cambiar sus nombres o signaturas rompe a cualquier
cliente que las use.

- `router_help()` — descripción textual + pasos siguientes.
- `router_status()` — estado actual: inventory, plugins, setup
  pendiente.
- `router_add_host(...)`
- `router_add_service(...)`
- `router_add_credential(...)`
- `setup_<plugin>()` — dinámica, una por plugin con requirements
  pendientes.
- `skill_<nombre>()` / `agent_<nombre>()` — desde discovery `.md`.

### Plugin

Un servidor MCP independiente cuyo manifest (`plugin.toml`) declara
cómo arrancarse, qué credenciales necesita, qué hosts del inventory
le hacen falta, y cómo se quiere exponer. El router lo monta como
**subserver** vía `create_proxy` y le prefija los tools con el
namespace del plugin.

Un plugin vive normalmente en su propio repositorio. El operador lo
clona (o symlinkea) bajo `plugins/<plugin-name>/` del framework
para que el router lo descubra.

### Skills / agents

Ficheros `.md` con frontmatter YAML (`name`, `description`). El
router los descubre en `skills_dir` y `agents_dir` (configurados en
`router.toml`) y los expone como `skill_<nombre>` o
`agent_<nombre>`. No son plugins — no tienen runtime, no spawnean
subprocess, simplemente devuelven contenido. Pertenecen al
ecosistema Claude (`~/.claude/`), fuera del scope del operador.

## 2. Convenciones de nombres

### Framework

| Qué | Forma | Ejemplo |
|-----|-------|---------|
| Paquete Python (`pyproject.toml` `[project].name`) | kebab-case | `mimir-mcp` |
| Import path | snake_case derivado | `mimir_mcp` |
| Repo GitHub | kebab-case, mismo que paquete | `<owner>/mimir-mcp` |
| Entrypoint CLI | corto | `mimir` o `router.py` |

### Plugin

| Qué | Forma | Regla | Ejemplo |
|-----|-------|-------|---------|
| `[plugin].name` en manifest | snake_case, minúsculas | regex `^[a-z][a-z0-9_]*$` | `proxmox`, `echo`, `pi_camera` |
| Tool namespace expuesto | `<plugin_name>_<tool_name>` | el router lo compone | `proxmox_list_nodes`, `echo_reverse` |
| Repo GitHub recomendado | `mcp-plugin-<tema>` | convención, no obligatoria | `mcp-plugin-proxmox`, `mcp-plugin-gpon` |
| Directorio bajo `plugins/` | igual que `[plugin].name` | obligatorio para discovery | `plugins/proxmox/` |

**Nota sobre `mcp-plugin-<tema>`:** no se impone, pero ayuda al
descubrimiento en GitHub y deja claro que un repo es un plugin de
este ecosistema. Repos que ya existen con otro nombre (por ejemplo
`<usuario>/<servicio>-mcp`) pueden mountarse igual — lo único que
cuenta es el `plugin.toml` del repo.

### Skill / agent

- Nombre en el frontmatter `.md` → sanitizado a snake_case para la
  tool.
- Tool expuesta: `skill_<name>` o `agent_<name>`.

## 3. Qué un plugin NO debe hacer

Si alguna de estas aparece en tu código, el plugin está atado a un
despliegue concreto y no es reutilizable.

- **No hardcodear IPs, hostnames o rutas.** Todo lo específico del
  operador vive en `inventory/*.yaml` y se pide al router vía
  `core.inventory.get_hosts(type=..., tag=...)`.
- **No leer `os.environ` directamente para secretos.** Declara el
  patrón en `[security].credential_refs` y el router inyectará las
  env vars que tu plugin esté autorizado a ver. Todo lo demás queda
  fuera de su proceso hijo.
- **No escribir en `.env` ni en `~/.ssh/*` sin pasar por
  `router_add_credential`.** El vault centraliza la escritura.
- **No asumir SO** sin checkearlo. Si tu plugin es Windows-only o
  Linux-only, documenta el contrato (`requires.os = "windows"`,
  pendiente de schema si hace falta) o detéctalo al arrancar y falla
  limpio.
- **No importar `core/*` del framework** desde el código del
  plugin. Rompe el aislamiento y bloquea la publicación del plugin
  como repo independiente.

## 4. Cómo encaja un plugin nuevo

El contrato mínimo es un directorio con `plugin.toml` + código
arrancable. Tres formas canónicas:

### Forma A — Script Python standalone

Más simple. Útil para plugins sin dependencias externas a
`fastmcp`. Ver `examples/echo-plugin/` — es literalmente esto.

```toml
[plugin]
name = "echo"
version = "1.0.0"

[runtime]
entry = "server.py"

[security]
credential_refs = []
```

### Forma B — `uv run` con proyecto Python completo

Cuando el plugin tiene `pyproject.toml` propio, dependencias
externas (requests, paramiko…) y quieres que `uv` gestione su venv.

```toml
[plugin]
name = "proxmox"
version = "1.0.0"

[runtime]
command = "uv"
args = ["run", "proxmox-mcp"]
# cwd del subprocess = directorio del plugin (automático)

[security]
credential_refs = ["PROXMOX_*"]

[[requires.hosts]]
type = "proxmox"
min = 1
prompt = "Necesito al menos un nodo Proxmox con token API."
```

### Forma C — `uvx` desde paquete publicado en PyPI

Cuando el plugin ya está publicado como paquete ejecutable y no
necesita checkout local.

```toml
[plugin]
name = "unifi"
version = "1.0.0"

[runtime]
command = "uvx"
args = ["unifi-mcp-server"]

[security]
credential_refs = ["UNIFI_*"]
```

### Placeholder `{plugin_dir}`

En los `args`, el string literal `{plugin_dir}` se sustituye por la
ruta absoluta del directorio del plugin en mount-time. Útil cuando
un comando necesita el path explícito:

```toml
[runtime]
command = "uv"
args = ["--directory", "{plugin_dir}", "run", "my-plugin"]
```

## 5. Plugin vs MCP paralelo

No todo tiene que ser plugin. Decidir entre **subserver mountado**
(plugin) y **MCP paralelo** (otro servidor MCP que el cliente
configura aparte del router):

### Cuándo plugin (subserver)

- La unidad se beneficia de compartir `inventory/`, `vault` y
  `audit` con el resto.
- Las tools se usan conversacionalmente junto con las tools del
  router y de otros plugins.
- El aislamiento por subprocess + env scoping es suficiente.

### Cuándo MCP paralelo

- Es una unidad muy pesada (GB de dependencias, modelo ML cargado en
  memoria) que no quieres que el router arranque aunque el operador
  no lo use.
- Necesita un aislamiento más fuerte que el del subprocess (p. ej.
  correr en otro host, otro usuario, otro container).
- Su ciclo de vida es completamente distinto: arranca y para por su
  cuenta, puede estar remoto.

Tener algo como plugin o como MCP paralelo no cambia la
arquitectura interna del framework: el cliente MCP ve ambos igual
(una lista de tools), solo cambia quién los hostea.

## 6. Checklist de un plugin publicable

Antes de publicar un repo `mcp-plugin-<tema>`:

- [ ] `plugin.toml` válido: pasa `parse_manifest(..., strict=True)`.
- [ ] `[plugin].name` en snake_case, sin prefijos corporativos.
- [ ] Cero IPs/hostnames/rutas en el código — todo pasa por
      `inventory/` o `credential_refs`.
- [ ] `README.md` con: qué hace, qué requirements tiene, ejemplo de
      `inventory/hosts.yaml` mínimo para hacerlo funcionar.
- [ ] Un smoke test que mounte el plugin y verifique que al menos
      una tool responde.
- [ ] Licencia clara.

## 7. Ejemplos vivos

- `examples/echo-plugin/` — la plantilla canónica.
- `docs/operator-notes/cutover/manifests/` — manifests reales del
  despliegue del autor, útiles como referencia de casos más
  complejos (requirements, credenciales, `uv run`, `uvx`).

## 8. Errores comunes

- **Nombre del plugin con mayúsculas o guiones.** El regex
  `^[a-z][a-z0-9_]*$` lo rechaza. Usa snake_case.
- **`credential_refs = "PROXMOX_*"`** (string en vez de lista).
  TOML lo acepta pero el router falla cerrado: ninguna credencial
  matchea. Debe ser `credential_refs = ["PROXMOX_*"]`.
- **Dos plugins declaran el mismo `[plugin].name`.** El router
  aplica last-wins pero loguea warning. Conviene evitarlo.
- **Importar `from core.secrets import ...` desde el código del
  plugin.** Funciona solo si el plugin comparte checkout con el
  framework; se rompe al publicar como repo independiente. Usa
  credenciales via env, no via import.
