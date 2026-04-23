# echo-plugin

El plugin más simple que se puede escribir para este framework: dos
tools sin dependencias externas (`echo`, `reverse`), un manifest
mínimo, cero credenciales.

Sirve para tres cosas:

1. **Plantilla** para plugins nuevos: copia el directorio, renombra,
   sustituye los tools y amplía `plugin.toml` con lo que tu plugin
   necesite (`credential_refs`, `[[requires.hosts]]`, etc.).
2. **Smoke test** del framework: si el router monta y expone este
   plugin correctamente, el ciclo manifest → mount → tool está vivo.
3. **Contrato vivo**: el test
   `tests/test_example_plugin.py` verifica este plugin contra
   `core.loader.parse_manifest` y `router._plugin_mount_config`, así
   que si alguien rompe el contrato del framework sin querer, el test
   grita antes del merge.

## Estructura

```
examples/echo-plugin/
├── plugin.toml     # identidad, runtime, security, tools
├── server.py       # FastMCP server con 2 tools
└── README.md       # este archivo
```

## Cómo se mounta desde el router

Opción 1 — symlink al checkout del framework:

```bash
cd /path/to/<NUEVO_NOMBRE>
ln -s $(pwd)/examples/echo-plugin plugins/echo
python router.py --dry-run
# Expected: plugin 'echo' listado con status=ok
```

Opción 2 — copiar bajo `plugins/`:

```bash
cp -r examples/echo-plugin plugins/echo
python router.py
# El cliente MCP verá echo_echo y echo_reverse
```

## Anatomía del manifest

Cada sección de `plugin.toml` responde a una pregunta concreta:

| Sección | Pregunta |
|---|---|
| `[plugin]` | ¿Quién eres? Nombre, versión, descripción, enabled. |
| `[runtime]` | ¿Cómo te arranco? Script Python (entry) o comando custom (command + args). |
| `[security]` | ¿Qué credenciales puedes leer? Lista de patrones fnmatch contra refs del vault. |
| `[tools]` | ¿Qué tools expongo al LLM? Whitelist/blacklist por nombre. |
| `[[requires.hosts]]` | ¿Necesitas hosts concretos en el inventory para funcionar? |
| `[[requires.credentials]]` | ¿Necesitas que existan ciertos secretos antes de activarte? |

El manifest del echo-plugin tiene las tres primeras y deja vacías las
de requisitos — no necesita infra.

## Extender este plugin

Si quieres añadir un tool nuevo, edita `server.py`:

```python
@mcp.tool
def shout(text: str) -> str:
    """Return the text uppercased."""
    return text.upper()
```

Sin tocar el manifest, el router automáticamente expondrá
`echo_shout`. Añadir dependencias externas significa editar
`plugin.toml` para meter un `[runtime].command = "uv"` +
`args = ["run", "echo-plugin"]` y añadir un `pyproject.toml` al
plugin — ver `docs/naming-guide.md` para el patrón completo.
