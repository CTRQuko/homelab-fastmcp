# Arquitectura

## Visión general

`homelab-fastmcp` es un **aggregator MCP** que expone una única interfaz stdio
a clientes MCP (Claude Desktop, OpenCode, etc.) y enruta cada tool al
downstream correspondiente mediante prefijos de namespace.

```
┌────────────────┐  stdio  ┌──────────────────────────┐
│ MCP Client     │ ◄─────► │ homelab-fastmcp          │
│ (Claude/Code)  │         │ (FastMCP Aggregator)     │
└────────────────┘         └─────────────┬────────────┘
                                         │
           ┌─────────────────────────────┼─────────────────────────────┐
           │                             │                             │
      ┌────▼────┐   ┌─────┐  ┌──────┐  ┌▼────┐  ┌────┐  ┌────┐  ┌────┐
      │ windows │   │linux│  │proxmo│  │unifi│  │uart│  │gpon│  │...native
      │  _*     │   │ _*  │  │x _*  │  │ _*  │  │_*  │  │_*  │  │ tools
      └─────────┘   └─────┘  └──────┘  └─────┘  └────┘  └────┘  └────┘
      Windows       Linux    Proxmox   UniFi    UART    GPON    tailscale_
      (subproc)     (ssh)    (api)     (api)    (serial)(ssh)   github_
                                                                uart_detectar_
```

## Componentes

### `server.py` (entrypoint)

- Carga `.env` vía `_parse_env_value()` (helper extraído, testeable)
- Determina `HOMELAB_DIR` y plataforma (`_ON_WINDOWS`)
- Inyecta defaults para `UNIFI_*` y `GPON_*`
- Monta 7 proxies con `create_proxy()` + `mcp.mount(namespace=...)`
- Registra 12 tools nativas con `@mcp.tool()`
- `main()` arranca `mcp.run(transport="stdio")` con manejo de `KeyboardInterrupt`

### `native_tools/secrets.py`

Loader con prioridad fija:

1. `os.environ[KEY]`
2. `$HOMELAB_DIR/.config/secrets/*.md` — líneas `KEY=value`
3. `$PROJECT_ROOT/.env` — mismo formato

API:
- `load(key) -> str` — lanza `RuntimeError` si no encuentra
- `load_optional(key, default="") -> str` — no lanza
- `mask(value, visible=4) -> str` — para logging seguro

### `native_tools/tailscale.py`

Cliente REST a `api.tailscale.com/api/v2`. 6 endpoints:
`list_devices`, `get_device`, `get_acls`, `get_dns`,
`authorize_device`, `delete_device`.

- Validación: `^[a-zA-Z0-9_-]{1,64}$` para `device_id` (defensa DoS)
- Sanitización de errores: 9 patrones (`tskey-*`, `bearer`, `token=`, …)
  → sustituyen excepciones con mensaje genérico

### `native_tools/github.py`

Wrapper sobre PyGithub. 5 funciones:
`list_repos`, `get_repo_info`, `get_issue`, `create_issue`, `list_prs`.

- Validación: `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` — rechaza nombres GitHub-inválidos
- Degradación: si no hay `GITHUB_TOKEN`, emite `UserWarning` y usa cliente anonymous

### `native_tools/uart_detect.py`

Detector de dispositivos en puertos serie. Protocolo:

1. Verifica que el puerto existe (fast-fail <500ms)
2. Abre `serial.Serial` y lee boot greeting (1.5s)
3. Detecta U-Boot en greeting → early return
4. Si no hay U-Boot, ejecuta 8 comandos de identificación:
   `uname -a`, `hostname`, `cat /proc/version`, `fw_printenv version`, …
5. `_settle` proporcional a `timeout_cmd` (defensa contra tiempos fijos)
6. Devuelve dict con `sistema`, `kernel`, `hostname`, `dispositivo`, `notas`

## Decisiones de diseño

### ¿Por qué FastMCP y no mcp-router Go?

- Reutilizamos los downstreams MCP ya existentes (Python + uvx)
- Menos superficie de deploy (un solo runtime Python)
- FastMCP 3.x soporta `create_proxy` + namespacing out-of-the-box

### ¿Por qué no cargar `downstream/servers.json`?

Experimento abandonado. Los configs están hardcoded en `server.py` porque:
- Permiten lógica condicional (`_ON_WINDOWS`)
- Permiten inyección dinámica de `env` desde `os.environ`
- Un JSON estático no puede expresar ambas cosas sin motor de templates

### ¿Por qué tools nativas en Python en vez de downstream?

Para 3 casos: UART, Tailscale, GitHub. Razones:
- **UART**: requiere acceso directo a pyserial en el proceso — no es operación de red
- **Tailscale**: solo 6 endpoints REST, un downstream es overkill
- **GitHub**: PyGithub ya hace el trabajo, no aporta un proceso extra

### Prefijos de namespace (`windows_*`, `uart_*`, …)

Decisión: `mcp.mount(proxy, namespace="unifi")` hace que todas las tools del
downstream aparezcan como `unifi_*` al cliente MCP. Esto evita colisiones
y permite al LLM elegir el downstream correcto por prefijo.

### Logging a stderr

`stdout` está reservado para el protocolo MCP (JSON-RPC). Cualquier `print()`
rompería la comunicación. Por eso `logging.basicConfig(stream=sys.stderr)`
y nunca usamos `print()` en el runtime.

## Flujo de arranque

```
1. Python importa server.py
2. logging.basicConfig() → stderr
3. _parse_env_value + lectura .env → pobla os.environ (respeta existentes)
4. Defaults UNIFI_* / GPON_*
5. Detecta _ON_WINDOWS
6. Crea FastMCP("Homelab Aggregator", instructions=...)
7. Monta 5-7 proxies (windows/docker solo Windows)
8. Importa native_tools.* y registra 12 tools nativas
9. main() → mcp.run(transport="stdio")
```

## Entry point y deployment

`pyproject.toml` declara:
```toml
[project.scripts]
homelab-fastmcp = "server:main"

[tool.setuptools]
py-modules = ["server"]
```

El `py-modules = ["server"]` es **necesario** porque `server.py` es un módulo
single-file, no un paquete. Sin él, setuptools no empaqueta `server` y
`uv run homelab-fastmcp` falla con `ModuleNotFoundError`.

Los clientes MCP (OpenCode, Claude Desktop) invocan así:

```
uv run --directory <path-al-proyecto> homelab-fastmcp
```

`--directory` hace que uv use el venv + entry points del proyecto,
independientemente del cwd desde donde se invoque el cliente MCP.

El server queda escuchando stdio indefinidamente; el cliente MCP gestiona
el ciclo de vida del subproceso (lanzarlo, enviar JSON-RPC, terminarlo).
`main()` captura `KeyboardInterrupt` y cualquier excepción con logs
estructurados a stderr.

## Tests

- `test_integration.py` arranca el servidor como subprocess real vía `Client(SERVER_PATH)`
- `test_resilience.py` verifica comportamiento ante fallos de downstream
- `test_adaptive.py` valida gating por plataforma
- `test_security.py` / `test_security_extended.py` cubren validación de inputs
- `test_critical.py` / `test_coverage_gaps.py` cubren contratos y regresiones
- `tests/manual/` excluido del pytest normal (`norecursedirs=["manual"]`)
