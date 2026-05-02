# Mimir

[![CI](https://github.com/CTRQuko/mimir-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/CTRQuko/mimir-mcp/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mimir-router-mcp.svg)](https://pypi.org/project/mimir-router-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/mimir-router-mcp.svg)](https://pypi.org/project/mimir-router-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

🇬🇧 [Read in English](README.md) · 🇪🇸 Estás leyendo la versión en español

> *"Mimir guarda el pozo de la sabiduría: aconseja a Odín cuando le falta
> contexto. Tu router MCP hace lo mismo con el LLM."*

**Mimir es un router MCP declarativo** construido sobre FastMCP 3.x.
Pones un `plugin.toml` junto a cualquier servidor MCP y Mimir lo
descubre, lo monta bajo su propio namespace, escopa sus credenciales,
filtra sus tools y expone la unión a tu cliente (Claude Desktop, un
agente en bucle, cualquier cosa que hable MCP por stdio).

Lo que diferencia a Mimir de otros aggregators MCP:

- **Contrato declarativo de plugin** — cada plugin trae su propio
  `plugin.toml` describiendo identidad, runtime, seguridad y
  requisitos. Sin config central que mantener sincronizada.
- **Inventario separado de los plugins** — los operadores describen
  su infraestructura en `inventory/*.yaml` (hosts, servicios). Los
  plugins le piden al router *"dame hosts de tipo X"*; nunca
  hardcodean IPs ni hostnames.
- **Onboarding guiado por LLM** — cuando un plugin necesita hosts o
  credenciales que el operador no ha provisto, el router expone una
  meta-tool `setup_<plugin>()`. El LLM guía al operador por lo que
  falta, conversación a conversación.
- **Seguridad en capas** — quarantine de manifests, audit log, vault
  scoped de credenciales, profile gate, whitelist/blacklist de tools,
  y env scoping cross-plugin en subprocess. Ver
  [`docs/es/security-model.md`](docs/es/security-model.md).

## Instalación rápida

```bash
git clone https://github.com/CTRQuko/mimir-mcp
cd mimir-mcp
uv sync
uv run python router.py --dry-run
```

El dry-run imprime lo que Mimir ve: inventory, plugins descubiertos,
skills/agents. Sin configuración solo sirve las meta-tools — eso es
intencional. Suelta tu primer plugin bajo `plugins/` y vuelve a
ejecutar.

## Hello world — el plugin mínimo

Mira [`examples/echo-plugin/`](examples/echo-plugin/) para ver el
contrato completo en ~30 líneas. Para mountarlo:

```bash
ln -s "$(pwd)/examples/echo-plugin" plugins/echo
uv run python router.py --dry-run
```

Salida:

```
[mimir] router — profile: default
[mimir] Core: inventory, secrets, audit, memory(noop)
[mimir] Inventory: 0 hosts, 0 services
[mimir] Plugins discovered: 1
[mimir] Skills: 0  Agents: 0
```

Ahora el cliente ve `echo_echo` y `echo_reverse` junto a las
meta-tools `router_*`.

## Cómo se compara

Hay varios aggregators MCP en el ecosistema. Mimir ocupa un hueco
concreto:

| Herramienta | Qué aporta | Dónde difiere Mimir |
|-------------|------------|---------------------|
| **FastMCP `mount()`** | Librería para montar subservers en código | Mimir añade discovery, schema de manifest y capas de seguridad encima |
| **MetaMCP** | Aggregator + middleware en Docker, jerarquía de tres niveles | Mimir es un router Python single-process, enfocado en contrato declarativo y onboarding guiado por LLM |
| **Local MCP Gateway** | Aggregator con UI web, OAuth, perfiles | Mimir es CLI-first y se centra en el contrato de plugin — sin UI, sin OAuth (todavía) |
| **mcp-proxy-server** | Rutea peticiones a backends | Mimir añade la capa de inventory y los requirements semánticos |
| **mxcp** | Construye servers MCP desde YAML/SQL/Python | Mimir agrega servers ya construidos; complementario, no competidor |

## Documentación

- [`docs/es/naming-guide.md`](docs/es/naming-guide.md) — convenciones
  canónicas para plugins, repos y tools.
- [`docs/es/plugin-contract.md`](docs/es/plugin-contract.md) —
  referencia completa del schema de `plugin.toml`.
- [`docs/es/inventory-schema.md`](docs/es/inventory-schema.md) — el
  formato YAML que el operador usa para declarar hosts y servicios.
- [`docs/es/security-model.md`](docs/es/security-model.md) — las
  siete capas de seguridad en detalle.
- [`docs/es/ARCHITECTURE.md`](docs/es/ARCHITECTURE.md) — cómo encajan
  el router, los módulos core y los plugins.
- [`docs/es/INSTALL.md`](docs/es/INSTALL.md) — caminos de instalación
  más allá del rápido de arriba.
- [`docs/es/quickstart.md`](docs/es/quickstart.md) — recorrido visual
  del onboarding guiado por LLM.
- [`docs/operator-notes/`](docs/operator-notes/) — notas de un
  despliegue real (el homelab del autor). Útil como ejemplo
  trabajado; **no** forma parte del contrato público.

> Las versiones en inglés conviven en [`docs/`](docs/) sin sufijo.
> Las traducciones al español viven bajo [`docs/es/`](docs/es/).

## Estado

Mimir es un framework funcionando con una suite de 280+ tests que
cubre los módulos core, el loader, el wiring del router, el modelo
de seguridad, el plugin de ejemplo y los manifests de cutover. Está
en uso por el autor contra un homelab real. La rama
`refactor/generify-naming` (este código) ya está fusionada en
`main` con el tag `v0.1.0`; la publicación en PyPI está pendiente
de credenciales.

## Objetivos del proyecto (y no-objetivos)

**Objetivos:**

- Un router pequeño y legible que cualquiera pueda auditar en una
  tarde.
- Plugins como servidores MCP verdaderamente independientes —
  utilizables solos o montados.
- Onboarding que un LLM pueda llevar a cabo con el operador.
- Defaults de seguridad sobre los que el operador puede razonar.

**No-objetivos:**

- Un PaaS completo o sandbox. Mimir confía en el SO para aislar
  procesos; la capa 5 nivel 2 (interceptores de filesystem / red /
  exec) está diferida hasta que llegue un sandbox real.
- UI web. El plano de control son CLI + LLM.
- Reemplazar a FastMCP. Mimir va construido encima.

## Licencia

MIT. Ver [`LICENSE`](LICENSE).

## Agradecimientos

- El equipo de [Model Context Protocol](https://modelcontextprotocol.io)
  en Anthropic por el estándar.
- [FastMCP](https://github.com/jlowin/fastmcp) por las piezas Python.
- A mi prima Claude por hacer la pregunta *"¿esto es para un homelab
  o para un ecosistema?"* en el momento justo.
