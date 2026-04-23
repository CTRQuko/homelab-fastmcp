# Issues con Downstreams MCP

## Proxmox - Error "Proxmox no configurado"

### Síntoma
```
Error de conexión: Proxmox no configurado. Revisa PROXMOX_HOST/USER/TOKEN en .env
```

### Diagnosis
1. **homelab-fastmcp** carga su propio `.env` correctamente (PROXMOX_HOST=192.168.1.2)
2. **homelab-mcp** tiene su propio `.env` que también carga bien
3. El problema: **FastMCP `create_proxy` no propaga variables de entorno** al proceso hijo por defecto

### Solución aplicada
Añadir `env` directamente en el config del downstream en `server.py`:

```python
_proxmox_config = {
    "mcpServers": {
        "default": {
            "command": "uv",
            "args": ["--directory", f"{HOMELAB_DIR}/mcp-servers/homelab-mcp", "run", "homelab-proxmox-mcp"],
            "env": {
                "PROXMOX_HOST": "192.168.1.2",
                "PROXMOX_USER": "claude@pam",
                "PROXMOX_TOKEN_NAME": "claude-api",
                "PROXMOX_TOKEN_VALUE": "<REDACTED — see .env>",
                "PROXMOX_NODES_FILE": "C:/homelab/mcp-servers/homelab-mcp/proxmox_nodes.json"
            },
        }
    }
}
```

### Pendiente
- [ ] Testear que el downstream recibe las variables
- [ ] Considerar cargar vars dinámicamente desde el .env de homelab-fastmcp en lugar de hardcodear

### Resuelto en el nuevo `router.py` (Fase 6d)

La segunda pendiente ya no aplica al router modular: cuando un plugin se
monta via `create_proxy`, el router construye el dict `env` automáticamente
a partir del manifest `[security].credential_refs` y del vault (`core.secrets`).
No hace falta hardcodear. Ver `router._plugin_subprocess_env` + sección
"Scoping for subprocess plugins" en `docs/security-model.md`.

`server.legacy.py` mantiene la solución vieja porque los clientes siguen
apuntando a él hasta Fase 8 (cutover).

## UniFi - 401 Unauthorized

### Síntoma
```
Failed to authenticate with UniFi API: Authentication failed: {"error":{"code":401,"message":"Unauthorized"}}
```

### Diagnosis
- La API key existe en entorno (ver `.env`, UNIFI_API_KEY)
- Posibles causas: key revocada, controller diferente, o la key ya no es válida

### Acción requerida
- Regenerar API key en UniFi Console
- Actualizar en `.env`

## Logs de errores

Los errores se registran en:
```
C:\homelab\laboratorio\homelab-fastmcp\failure.log
```
