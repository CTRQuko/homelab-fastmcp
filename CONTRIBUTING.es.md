# Contribuir a Mimir

🇬🇧 [Read in English](CONTRIBUTING.md) · 🇪🇸 Estás leyendo la versión en español

Gracias por considerar contribuir. Mimir es pequeño y trata de
seguirlo siendo — el valor está en los contratos, no en el volumen
de código.

## Qué tipo de contribuciones son bienvenidas

Aproximadamente en orden de urgencia:

1. **Plugins**. Reales. Mimir es interesante en proporción al
   ecosistema de plugins que pueda mountar. Si publicas un
   servidor MCP, suelta un `plugin.toml` y avísanos; lo añadimos
   a la lista.
2. **Reportes de compatibilidad**. Si corres Mimir contra un
   cliente que no hayamos validado todavía (Cline, Roo Code,
   Cursor, un agente interno…) abre un PR añadiendo una fila a
   [`docs/compatibility.md`](docs/compatibility.md).
3. **Reportes de bugs** con reproducción — ver las plantillas de
   issue.
4. **Arreglos de documentación**. Especialmente donde el framework
   todavía huela al homelab del autor.
5. **Fixes en core**. Validación de schema, modelo de seguridad,
   audit, env scoping — ver
   [`docs/security-model.md`](docs/security-model.md) para que un
   cambio no debilite por accidente una capa de defensa.

Lo que está **fuera de scope** (no se mergea):

- UI web / dashboard. Mimir es CLI-first; el LLM es la UX.
- Plugins de dominio bundled (Proxmox, GitHub, …). Esos viven en
  sus propios repos. Referéncialos, no los traigas.
- Acoplamiento a un cliente concreto (Claude Desktop, Cursor, …).
  Mimir habla MCP por stdio; ese es el contrato.

## Setup

```bash
git clone https://github.com/CTRQuko/mimir-mcp
cd mimir-mcp
uv sync --extra test
uv run --extra test pytest tests/ -q
uv run python router.py --dry-run
```

Si los dos pasan, estás listo. La suite `tests/` debe seguir
verde en cada commit.

## Escribir un plugin

Lee [`docs/naming-guide.md`](docs/naming-guide.md) y copia
[`examples/echo-plugin/`](examples/echo-plugin/) como plantilla.
El contrato es el schema de `plugin.toml` en
[`docs/plugin-contract.md`](docs/plugin-contract.md).

Tres reglas que un plugin debe respetar:

- **No hardcodear infraestructura**. IPs, hostnames, paths — nada
  de eso vive en el código del plugin. O declara un
  `[[requires.hosts]]` y deja que el inventory del operador lo
  provea, o acepta una credential ref.
- **No leer `os.environ` directamente para secretos**. Declara un
  patrón `credential_refs` en `[security]` — Mimir inyecta las
  vars que casen al subprocess y solo esas.
- **No importar de `core/`**. Los plugins viven en sus propios
  repos y tienen que funcionar standalone. Habla con Mimir a
  través del manifest y de las env vars que inyecta, no a través
  de internals del framework.

## Estilo de código

- Python 3.11+. Usamos `from __future__ import annotations` en
  todas partes.
- Type hints en funciones públicas.
- Los tests viven al lado del módulo que cubren (`core/foo.py` →
  `tests/test_core_foo.py`).
- Añade un test antes de arreglar un bug. El fix es el segundo
  commit; el test que falla es el primero.

## Pull requests

Los PRs pequeños se mergean rápido. Los PRs grandes se sientan
hasta que el autor tenga tiempo de revisarlos como toca. Si tu
cambio es mayor a ~300 líneas, abre primero un issue para discutir
la forma.

Un PR limpio tiene:

- Un diff enfocado — un concepto por commit.
- Una descripción que explica el *por qué*, no el *qué* (el diff
  ya muestra el qué).
- Tests para el cambio de comportamiento.
- Una bullet bajo "Unreleased" en
  [`CHANGELOG.md`](CHANGELOG.md) si el cambio es visible al
  usuario.

CI debe estar verde antes del merge.

## Reportes de seguridad

No abras issues públicas para bugs de seguridad. Email al
maintainer (ver [SECURITY.md](docs/security-model.md) para el
contacto; si no hay, la función "private vulnerability reporting"
de GitHub vale).

## Código de conducta

Este proyecto sigue el [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
Sé excelente con los demás.
