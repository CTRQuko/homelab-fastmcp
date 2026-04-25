# Compatibilidad de clientes

🇬🇧 [Read in English](../compatibility.md)

Una matriz de clientes MCP contra los que hemos validado Mimir.
Mimir habla el transport MCP stdio estándar, así que cualquier
cliente que cumpla la spec debería funcionar — pero la realidad es
más sucia que la spec, y los clientes a veces filtran nombres de
tools, se atragantan con unicode, o se saltan notificaciones
`tools/list_changed`. Esta página es la verdad empírica.

Si corres Mimir contra un cliente que no listamos, abre un PR
añadiendo una fila (plantilla al final). Una línea que diga
*"funciona en Cline 0.4.2 contra Mimir 0.1.0"* es una
contribución útil.

## Status

Validado end-to-end significa:

1. El cliente carga la config del router sin errores al arrancar.
2. `tools/list` devuelve las meta-tools `router_*` más cualquier
   namespace de plugin que deba ser visible.
3. `router_help()` y `router_status()` devuelven sus payloads
   esperados cuando el LLM (o el operador a mano) las llama.
4. Una tool no trivial de plugin (e.g. `echo_reverse` de
   `examples/echo-plugin/`) round-trippea argumentos y resultado.

| Cliente                     | Versiones probadas | Status        | Notas |
|-----------------------------|--------------------|---------------|-------|
| Claude Desktop              | —                  | No validado   | Estable contra el server legacy; se espera que funcione contra Mimir, sin ejercicio end-to-end. |
| Claude Code CLI             | —                  | No validado   | El harness agentic principal del autor. A ejercitar primero. |
| MCP Inspector               | —                  | No validado   | Recomendado para validación a nivel protocolo. |
| Zed                         | —                  | No validado   | Tiene soporte MCP nativo; comportamiento con tools dinámicas `setup_<plugin>()` no verificado. |
| Cursor                      | —                  | No validado   | — |
| Cline (VS Code)             | —                  | No validado   | — |
| Roo Code (VS Code)          | —                  | No validado   | — |
| Kilo Code                   | —                  | No validado   | — |

La matriz está intencionalmente abierta — la mayoría de filas
quedan vacías hasta que alguien corra la validación. **Help wanted**.

## Cómo validar un cliente

1. Configura el cliente para arrancar Mimir por stdio. La forma
   exacta depende del cliente; en la práctica es el snippet de
   [`docs/es/INSTALL.md`](INSTALL.md) adaptado al fichero de
   config del cliente.
2. Mounta el plugin de ejemplo para tener al menos una tool
   no-router que ejercitar:

   ```bash
   ln -s "$(pwd)/examples/echo-plugin" plugins/echo
   ```

3. Abre una sesión en el cliente. Verifica:

   - El cliente lista las tools (en UIs agentic suele ser el
     panel "tools" o "MCP").
   - Ves `router_help`, `router_status`, `router_list_plugins`,
     y `echo_echo` / `echo_reverse`.

4. Lleva cada una a través del LLM:

   - *"Muéstrame qué puede hacer Mimir"* → debería llamar
     `router_help()`.
   - *"Lista los plugins"* → `router_list_plugins()`.
   - *"Invierte la cadena `mimir`"* →
     `echo_reverse({"text":"mimir"})` → devuelve `"rimim"`.

5. Si algo se porta mal, captura el error exacto del log del
   cliente *y* del `config/audit.log` (lado Mimir). La
   combinación es mucho más útil que cualquiera por separado.

## Cómo añadir una fila

Abre un PR editando este fichero. Usa esta plantilla:

```markdown
| Cliente | Versión(es) | Status   | Notas              |
|---------|-------------|----------|--------------------|
| <nombre>| <versiones> | <status> | <notas libres>     |
```

`status` debería ser uno de:

- **Validado** — los cuatro pasos de arriba pasaron.
- **Validado con caveat** — funciona, pero tiene una idiosincracia
  documentada (nombres de tools truncados, manejo de unicode,
  arranque lento, etc.).
- **Roto** — no funciona hoy; por favor enlaza un issue con el
  fallo exacto.
- **No validado** — placeholder; nadie ha corrido la validación
  todavía.

## Caveats conocidos (cualquier cliente)

Estas son limitaciones enraizadas en MCP mismo, no en ningún
cliente concreto:

- **Los nombres de tools son estables por sesión.** Un plugin que
  pase a `ok` después de que `setup_<plugin>()` corra no puede
  inyectar sus tools a mitad de sesión en la mayoría de los
  clientes. El operador reinicia la sesión para ver el set
  completo.
- **Los valores de credenciales fluyen por el transcript del
  cliente.** Cuando el LLM llama
  `router_add_credential(ref, value)`, el valor aparece en el
  historial de chat del cliente porque el transport MCP no está
  cifrado a nivel aplicación. Para evitar esto, escribe la
  credencial al vault out-of-band (fichero o env var) y haz que
  el LLM solo confirme que la ref existe.
- **Las descripciones largas de tools pueden truncarse.** Algunos
  clientes imponen un budget por descripción. Los autores de
  plugin deberían mantener las descripciones bajo ~200 caracteres
  para portabilidad.
