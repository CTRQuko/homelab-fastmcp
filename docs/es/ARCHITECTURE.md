# Arquitectura de Mimir

🇬🇧 [Read in English](../ARCHITECTURE.md)

Un router MCP single-process en Python construido sobre FastMCP 3.x.
La forma:

```
┌─────────────┐  stdio  ┌────────────────────────────┐
│ Cliente MCP │ ◄─────► │ router.py (Mimir)          │
│ (Claude     │         │  ├── core/                 │
│  Desktop,   │         │  │   secrets, audit,       │
│  agente…)   │         │  │   inventory, memory,    │
└─────────────┘         │  │   loader, profile,      │
                        │  │   skills                │
                        │  ├── meta-tools (router_*) │
                        │  ├── tool middleware       │
                        │  └── plugins mountados     │
                        │      via create_proxy      │
                        └────────────────────────────┘
                              ▲   ▲   ▲   ▲
                              │   │   │   │   subprocess
                              │   │   │   │   spawneado por
                              │   │   │   │   plugin
                              ▼   ▼   ▼   ▼
                        ┌────┐┌────┐┌────┐┌────┐
                        │ p1 ││ p2 ││ p3 ││ pN │  plugins
                        └────┘└────┘└────┘└────┘
```

## Piezas

### `router.py`

Entry point. Al arrancar construye un `RouterState` (config +
inventory + plugins descubiertos), luego ensambla una instancia de
`FastMCP` con:

- **Meta-tools** prefijadas `router_*` y `setup_<plugin>()` para que
  el LLM dirija el onboarding.
- **Plugins mountados** como subservers FastMCP via `create_proxy`,
  cada uno bajo su propio namespace.
- **Skills/agents** descubiertos como ficheros `.md` con frontmatter,
  expuestos como `skill_<nombre>` / `agent_<nombre>`.
- **Tool policy middleware** que filtra las declaraciones
  `[tools].whitelist/blacklist` en `on_list_tools` y `on_call_tool`.

### `core/`

Las piezas internas agnósticas a infraestructura. Ninguna expone
tools directamente — las consume el router y, indirectamente, los
plugins:

- `secrets` — vault scoped de credenciales.
  `get_credential(ref, ctx)` con allowlist declarada en el manifest.
- `audit` — log JSONL append-only de cada llamada a tool.
- `inventory` — lector tipado de `Host` / `Service` para
  `inventory/*.yaml`.
- `memory` — patrón adapter; `noop` y `sqlite` implementados,
  `engram` / `claude_mem` diferidos.
- `loader` — parser de manifest, evaluación de requirements, diff
  de reconciliación, quarantine para `plugin.toml` malformado.
- `profile` — lee la allowlist
  `profiles/<nombre>.yaml:enabled_plugins`.
- `skills` — descubre ficheros `.md` con frontmatter YAML.

### Plugins (bajo `plugins/`)

Cada plugin es un directorio con al menos un `plugin.toml`. El
router spawnea cada plugin como subprocess via `create_proxy` de
FastMCP y prefija sus tools con el namespace del plugin. El env del
subprocess se construye explícitamente: las env vars de sistema
pasan; las vars con forma de credencial solo se propagan cuando el
manifest del plugin las reclama (ver `docs/es/security-model.md`
para el scoping cross-plugin).

Los plugins son independientes: cada uno puede correr standalone
como servidor MCP. Mimir los agrega pero no los posee.

### Inventory (bajo `inventory/`)

La descripción declarativa del operador sobre su infraestructura.
`hosts.yaml` lista hosts (tipo, dirección, método de auth, ref de
credencial, tags); `services.yaml` lista servicios y los ata a los
hosts. Los plugins le piden al router *"dame hosts de tipo X"* —
nunca leen este directorio directamente.

### Profiles (bajo `profiles/`)

`<nombre>.yaml:enabled_plugins` es una allowlist estricta. El
perfil default no carga ningún plugin; el operador crea perfiles
adicionales para activar los que quiera.

## La secuencia de arranque

1. Parsea `config/router.toml` → `RouterConfig`.
2. Carga inventory desde `inventory/*.yaml` → `Inventory`.
3. Descubre plugins bajo `plugins/`:
   - Parsea cada `plugin.toml`. Malformado → quarantine.
   - Evalúa requirements contra el inventory → fija el estado
     (`ok` / `pending_setup` / `disabled` / `error`).
4. Aplica el profile gate: descarta plugins que no estén en
   `enabled_plugins`.
5. Bootstrappea la instancia FastMCP:
   - Registra meta-tools `router_*` y `setup_<plugin>()`.
   - Mounta cada plugin `ok` via `create_proxy` con env scoped.
   - Descubre skills/agents y registra una tool por fichero.
   - Engancha el tool policy middleware.
6. Sirve por stdio (o imprime y sale si `--dry-run`).

## Dónde vive el contrato

- **`plugin.toml`** — el schema está en
  [`docs/es/plugin-contract.md`](plugin-contract.md). El plugin de
  ejemplo en [`examples/echo-plugin/`](../../examples/echo-plugin/)
  es la referencia mínima viva.
- **`inventory/*.yaml`** — schema en
  [`docs/es/inventory-schema.md`](inventory-schema.md).
- **Naming** — convenciones en
  [`docs/es/naming-guide.md`](naming-guide.md).
- **Capas de seguridad** — desglose completo en
  [`docs/es/security-model.md`](security-model.md).

## Fuera del alcance (hoy)

- Sandbox de procesos más allá de lo que da el SO. La capa 5 nivel 2
  (interceptores filesystem / red / exec) está diferida hasta que
  llegue un sandbox real.
- UI web o API REST. Stdio + LLM son los planos de control
  soportados.
- Gestión de venv del plugin (`[runtime].venv = "auto"` +
  `deps = [...]` install). Por ahora los plugins traen su propio
  entorno.
