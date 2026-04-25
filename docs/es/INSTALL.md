# Instalar Mimir

🇬🇧 [Read in English](../INSTALL.md)

Tres caminos según lo que quieras hacer.

## 1. Correr desde un checkout (recomendado por ahora)

La rama donde vive este código todavía no está en PyPI. Clona el
repo y arranca con `uv`:

```bash
git clone https://github.com/CTRQuko/mimir-mcp
cd mimir-mcp
uv sync
uv run python router.py --dry-run
```

El dry-run imprime lo que Mimir expondría: inventory, plugins,
skills/agents. Sin configuración solo sirve las meta-tools `router_*`
— ese es el baseline vacío.

## 2. Añadir Mimir a un cliente MCP

Cuando el dry-run se ve bien, apunta tu cliente MCP a `router.py`.
Mismo patrón para cualquier cliente que soporte servidores MCP por
stdio.

Ejemplo (config de Claude Desktop —
`%APPDATA%\Claude\claude_desktop_config.json` en Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json`
en macOS):

```json
{
  "mcpServers": {
    "mimir": {
      "command": "uv",
      "args": [
        "--directory",
        "/ruta/absoluta/a/mimir-mcp",
        "run",
        "python",
        "router.py"
      ]
    }
  }
}
```

Reinicia el cliente. La primera tool que verás es `router_help` —
llámala desde el LLM y te guiará el resto.

## 3. Mountar tu primer plugin

Los plugins son servidores MCP con un `plugin.toml`. El ejemplo
mínimo viene con el framework:

```bash
ln -s "$(pwd)/examples/echo-plugin" plugins/echo
uv run python router.py --dry-run
```

Para manifests del mundo real (paquetes Python con uv, uvx,
credenciales declaradas, requisitos de hosts), mira
[`docs/operator-notes/cutover/manifests/`](../operator-notes/cutover/manifests/).

## Ficheros de configuración

- **`config/router.toml`** — config del framework (perfil, rutas a
  plugin dir / inventory dir / skills dir, elección de memory
  backend). Opcional: los defaults funcionan si no existe.
- **`inventory/hosts.yaml`** + **`inventory/services.yaml`** — tu
  infraestructura declarativa. Las plantillas están al lado como
  `*.yaml.example`.
- **`profiles/<nombre>.yaml`** — allowlist explícita de plugins para
  ese perfil. El perfil default no carga ningún plugin; crea el
  tuyo para activarlos.
- **`<MIMIR_HOME>/secrets/*.md`** — fichero opcional de vault con
  líneas `KEY=value`. `MIMIR_HOME` es la env var que apunta Mimir
  a su raíz de config; el default sigue convenciones de plataforma
  (`%APPDATA%/mimir` en Windows o `$XDG_CONFIG_HOME/mimir`,
  típicamente `~/.config/mimir`, en el resto). El nombre legacy
  `HOMELAB_DIR` aún se acepta con un DeprecationWarning para que
  instalaciones que vienen de prototipos anteriores no se rompan.

Mira [`docs/es/inventory-schema.md`](inventory-schema.md) para las
formas YAML y [`docs/es/security-model.md`](security-model.md) para
cómo fluyen las credenciales.

## Comprobaciones rápidas

```bash
# Tests
uv run --extra test pytest tests/ -q

# Dry-run muestra el estado sin servir
uv run python router.py --dry-run
```

Si las dos pasan y el dry-run imprime `[mimir] router — profile: …`,
la instalación está bien.
