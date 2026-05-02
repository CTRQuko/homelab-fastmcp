# Diagnóstico histórico — Plugin homelab pending_setup (2026-04-27)

> **Estado:** ✅ RESUELTO en versiones posteriores. Documento conservado como
> referencia histórica de los gaps que motivaron las features descritas abajo.
>
> **Resoluciones:**
> - **Problema 1 + 2** (nombres de variable / formato token) → resueltos al
>   adoptar el patrón `PROXMOX_NODES_FILE` (manifest del plugin homelab) y
>   `core.secrets.resolve_credential_refs` con globs `PROXMOX_*`.
> - **Problema 3** (validación solo miraba `os.environ`) → **fixed v0.3.1**.
>   `core/loader._check_requirement` ahora usa `list_candidate_refs()` que
>   agrega env + vault file + `.env`. Ver CHANGELOG v0.3.1.
> - **Problema 4** (multi-nodo requiere JSON) → soportado vía
>   `PROXMOX_NODES_FILE`. Schema validation del JSON añadida en plugin
>   homelab v1.3.0 (fail-loud al boot).
>
> Si llegas aquí buscando un gap actual: ver `framework-deferrals.md`.

---

## Contexto original (2026-04-27)

**Estado en su momento:** `pending_setup` — credenciales añadidas pero plugin no activa

Se añadieron 9 credenciales (`PROXMOX_PVE_*`, `PROXMOX_PVE2_*`, `PROXMOX_PVE3_*`)
pero el plugin seguía en `pending_setup`. Tres problemas independientes apilados.

### Problema 1 — Nombres de variable incorrectos

El plugin leía env vars con nombres fijos definidos en `homelab_mcp/config.py`:

| Variable esperada | Lo que se añadió |
|---|---|
| `PROXMOX_HOST` | `PROXMOX_PVE_HOST` |
| `PROXMOX_USER` | `PROXMOX_PVE_USER` |
| `PROXMOX_TOKEN_NAME` | `PROXMOX_PVE_TOKEN` *(además, formato incorrecto)* |
| `PROXMOX_TOKEN_VALUE` | *(no añadido)* |

`PROXMOX_PVE_HOST` pasaba el glob `PROXMOX_*`, pero al arrancar el subproceso,
`os.getenv("PROXMOX_HOST")` devolvía vacío.

### Problema 2 — Formato del token incorrecto

La API de Proxmox usa el token combinado (`tokenid=secret`). El plugin lo
esperaba dividido en dos variables:

```
# En apispve.md:
claude@pam!claude-api=3a6dd437-24b3-4c7e-97da-b100da349f69

# Lo que esperaba el plugin:
PROXMOX_TOKEN_NAME=claude-api          ← solo el ID del token
PROXMOX_TOKEN_VALUE=3a6dd437-24b3-4c7e-97da-b100da349f69
```

### Problema 3 — La validación solo miraba `os.environ`, no el vault

Root cause de por qué `setup_homelab` reportaba `pending_setup` aunque las
credenciales estuvieran en keyring/vault.

En `core/loader.py` la validación era:

```python
for name in os.environ:                          # ← solo env vars vivas
    if fnmatch.fnmatchcase(name, pattern) and has_credential(name):
        return True
return False                                     # ← siempre llega aquí si solo hay vault
```

`router_add_credential` escribía en el vault file (`%APPDATA%/mimir/secrets/router_vault.md`),
pero ese vault no se reflejaba en `os.environ` antes de la validación.

**Fix:** v0.3.1 añadió `list_candidate_refs()` que agrega env + vault file + `.env`.

### Problema 4 — Multi-nodo requiere archivo JSON

El plugin soporta dos modos:

| Modo | Variables | Nodos |
|---|---|---|
| Simple | `PROXMOX_HOST`, `PROXMOX_USER`, `PROXMOX_TOKEN_NAME`, `PROXMOX_TOKEN_VALUE` | 1 solo |
| Multi-nodo | `PROXMOX_NODES_FILE` apuntando a un JSON | N nodos |

Estructura del JSON (formato actual):

```json
{
  "pve":  {"host": "10.0.1.2:8006",  "user": "claude@pam", "token_name": "claude@pam!claude-api", "token_value": "...", "endpoint_node": "logrono"},
  "pve2": {"host": "10.0.1.3:8006",  "user": "claude@pam", "token_name": "claude@pam!claude-api", "token_value": "...", "endpoint_node": "pve2"},
  "pve3": {"host": "192.168.2.4:8006","user": "claude@pam", "token_name": "claude@pam!claude-ai",  "token_value": "...", "endpoint_node": "munilla"}
}
```

(Nota: las IPs originales del documento eran `192.168.1.x` — actualizadas a
`10.0.1.x` tras la migración de redes 2026-05.)

---

## Configuración resultante (vigente)

`C:\homelab\.mcp.json` apunta a `PROXMOX_NODES_FILE`:

```json
{
  "mcpServers": {
    "mimir-mcp": {
      "command": "C:\\homelab\\mcp-servers\\mimir-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\homelab\\mcp-servers\\mimir-mcp\\router.py"],
      "env": {
        "PROXMOX_NODES_FILE": "C:\\homelab\\.config\\secrets\\proxmox_nodes.json"
      }
    }
  }
}
```

El JSON contiene tokens — cubierto por `.gitignore` en `.config/secrets/`.

---

## Sobre credenciales SSH/sudo (no resuelto, fuera de scope del plugin homelab)

El plugin homelab actual solo expone tools de API Proxmox. Para SSH/sudo del
usuario `claude` (`C14ud3`) no hay patrón en el manifest del plugin homelab.
La feature `homelab_ssh_run` añadida en v1.4.0 del plugin homelab cubre el
caso canónico de SSH+sudo a hosts proxmox usando `claude-sudo.key` directamente
desde el filesystem, sin necesidad de entrada en el vault de mimir.
