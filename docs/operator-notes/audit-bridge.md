# Audit-bridge — flujo automático audit.log → runtime-issues.md

Mimir-mcp escribe cada invocación de tool a `config/audit.log` (JSONL).
El script `scripts/audit_to_runtime_issues.py` lee ese log, filtra
entries con `status != "ok"`, agrupa por `(plugin, tool, error_message)`
y añade un skeleton entry a `docs/operator-notes/runtime-issues.md`
para que el operador (o el siguiente asistente) complete causa / fix /
prevención.

Es la unión entre el log estructurado (máquina) y el registro narrativo
de incidentes (humano).

## Modos de uso

### Modo manual (one-shot)

```bash
python scripts/audit_to_runtime_issues.py --since "2 hours ago" --dry-run
```

`--dry-run` imprime los entries sin tocar disco. Útil para inspeccionar
qué se añadiría antes de comprometerlo.

### Modo automático (Claude Code Stop hook) — RECOMENDADO

El cliente Claude Code dispara hooks en eventos del ciclo de vida.
Configurar `Stop` en `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python C:/homelab/mcp-servers/mimir-mcp/scripts/audit_to_runtime_issues.py --state-file C:/homelab/LOGS/audit-bridge.state --append-to C:/homelab/mcp-servers/mimir-mcp/docs/operator-notes/runtime-issues.md --session-tag claude-code-stop"
          }
        ]
      }
    ]
  }
}
```

Resultado: cada vez que Claude Code cierra una sesión, el script corre
y registra los errores nuevos.

### Modo automático (cron / OpenCode)

Si tu cliente NO soporta hooks (e.g. OpenCode CLI), un cron horario es
equivalente:

```powershell
# Windows Task Scheduler — script PS1 que corre cada hora
$mimir = "C:\homelab\mcp-servers\mimir-mcp"
python "$mimir\scripts\audit_to_runtime_issues.py" `
    --state-file "C:\homelab\LOGS\audit-bridge.state" `
    --append-to "$mimir\docs\operator-notes\runtime-issues.md" `
    --session-tag "cron-hourly-$(Get-Date -Format 'yyyyMMdd-HH00')"
```

## Idempotencia — `--state-file`

El argumento `--state-file` señala un archivo que el script usa para
recordar el timestamp del último error procesado. Próxima ejecución
solo procesa errores POSTERIORES a ese timestamp — no se duplican
entradas.

Si el archivo no existe, el script cae a `--since` (o
`--since-session-start`) y crea el state-file tras el append exitoso.

Path estándar en Windows: `C:\homelab\LOGS\audit-bridge.state`. Es un
archivo de 1 línea con el float epoch del último ts procesado.

## Verificación end-to-end

1. **Provocar un error**: invocar una tool de mimir que falle
   (`router_install_plugin` con allow=false, `homelab_ssh_run` con
   host inválido, etc.). Aparece en `config/audit.log` con
   `status="error"`.
2. **Disparar el bridge**: cerrar Claude Code (si tienes el hook
   Stop) o esperar al cron, o ejecutar el script a mano.
3. **Verificar**: `runtime-issues.md` ganó un entry nuevo bajo la
   sesión `claude-code-stop` (o la tag que pasaste). Marcadores
   `<pendiente>` en causa / fix / prevención esperan tu input.

## Lo que el bridge NO hace

- **No completa causa/fix/prevención automáticamente**. Es un
  esqueleto — el humano (o un asistente con contexto) lo rellena.
- **No filtra success entries**. Solo registra `status != "ok"`.
- **No es retroactivo cross-archive**. Si rotaste `audit.log`,
  los datos viejos quedan en el archivo rotado; el bridge ve solo
  el activo.

## Rollback

Si una iteración del bridge añade entries no deseados:

```bash
# El script siempre hace backup del runtime-issues.md antes de append
mv docs/operator-notes/runtime-issues.md.bak docs/operator-notes/runtime-issues.md
# Y resetear state si quieres re-procesar desde un punto anterior
rm C:/homelab/LOGS/audit-bridge.state
```

## Histórico

- `v0.5.0` (2026-05-06) — script `audit_to_runtime_issues.py` introducido
  con `--state-file` idempotente, `--since-session-start`, `--session-tag`.
- `2026-05-08` — hook `Stop` documentado y probado end-to-end. Path
  standard `C:\homelab\LOGS\audit-bridge.state` para Windows operators.
