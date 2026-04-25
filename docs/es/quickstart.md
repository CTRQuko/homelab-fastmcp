# Quickstart — cómo se siente el onboarding

🇬🇧 [Read in English](../quickstart.md)

Este documento muestra la experiencia para la que Mimir está
diseñado: un operador arranca el router sin configuración y el LLM
le guía paso a paso, conversación a conversación. Sin editar YAML a
mano, sin crear ficheros, sin documentación profunda antes de la
primera llamada útil.

El transcript de abajo es ilustrativo — la frase exacta depende del
LLM y del cliente — pero las **llamadas a tools** son reales. Cada
invocación `router_*` y `setup_*` está cableada en el framework y
cubierta por `tests/test_integration_e2e.py`.

## Paso 0 — Instalar y arrancar

```bash
pip install mimir-mcp
# o, desde un checkout: uv run python router.py
```

Configura tu cliente MCP (Claude Desktop, Cursor, Zed, …) para
arrancar Mimir por stdio (ver [`docs/es/INSTALL.md`](INSTALL.md)).
Reinicia el cliente. Eso es todo el setup pre-LLM.

## Paso 1 — Primer contacto

> **Operador** *(al LLM en el cliente)*: Acabo de instalar Mimir.
> ¿Qué puede hacer?

> **LLM**: Lo compruebo.

```jsonc
// El LLM llama
router_help()
```

```jsonc
// Mimir devuelve
{
  "name": "mimir",
  "purpose": "Mimir is a declarative MCP router. Users describe…",
  "available_bootstrap_tools": [
    "router_status", "router_help",
    "router_add_host", "router_add_service", "router_add_credential",
    "router_install_plugin", "router_remove_plugin",
    "router_enable_plugin", "router_disable_plugin",
    "router_list_plugins"
  ],
  "next_steps": [
    "Call router_status() to see what's currently configured.",
    "Add hosts and services via router_add_host / router_add_service."
  ]
}
```

> **LLM**: Mimir está corriendo sin plugins todavía. ¿Quieres que
> instale alguno? Dime un servicio y me encargo del resto.

## Paso 2 — Instalar un plugin (modo strict, default)

> **Operador**: Quiero tools de Proxmox.

> **LLM**: Instalo el plugin de Proxmox.

```jsonc
router_install_plugin(source="github:CTRQuko/homelab-mcp")
```

```jsonc
{
  "action": "manual_instruction",
  "command": "git clone https://github.com/CTRQuko/homelab-mcp.git /…/plugins/homelab",
  "hint": "Run the command above on your machine. After it completes, restart Mimir."
}
```

> **LLM**: No puedo correr comandos shell yo solo en modo strict
> (default por seguridad). Lanza este comando en una terminal y
> avísame cuando termine:
>
> `git clone https://github.com/CTRQuko/homelab-mcp.git plugins/homelab`

> **Operador**: Hecho.

> **LLM**: Reinicia Mimir para que descubra el plugin nuevo.
> *(El operador reinicia el cliente, que respawnea Mimir.)*

## Paso 3 — Mimir detecta lo que falta

> **Operador**: ¿Cómo está el estado ahora?

```jsonc
router_status()
```

```jsonc
{
  "memory_backend": "noop",
  "inventory": { "hosts_total": 0, "services_total": 0 },
  "plugins": [
    {
      "name": "homelab",
      "version": "1.0.0",
      "status": "pending_setup",
      "missing": [
        { "kind": "hosts", "detail": { "type": "proxmox", "min": 1 },
          "prompt": "At least one Proxmox node with an API token…" },
        { "kind": "credentials", "detail": { "pattern": "PROXMOX_*" },
          "prompt": "Proxmox credentials (PROXMOX_<NODE>_HOST / _USER / _TOKEN)…" }
      ]
    }
  ],
  "setup_pending": ["homelab"]
}
```

> **LLM**: El plugin de Proxmox necesita un nodo y credenciales.
> Te pregunto lo que tienes.
>
> ```jsonc
> setup_homelab()
> ```
>
> Devuelve: *"Necesito: 1 nodo Proxmox + credenciales que casen con
> PROXMOX_\*."*
>
> ¿Cuál es la IP de tu nodo Proxmox?

## Paso 4 — Operador responde, LLM escribe el inventory

> **Operador**: 192.0.2.10, puerto 8006, nombre pve1.

> **LLM**: Anotado.

```jsonc
router_add_host(
  name="pve1",
  type="proxmox",
  address="192.0.2.10",
  port=8006,
  credential_ref="PROXMOX_PVE1_TOKEN",
  auth_method="api_token"
)
```

```jsonc
{ "added": "pve1", "path": "inventory/hosts.yaml" }
```

> **LLM**: Ahora necesito el token API. Créalo en la UI de Proxmox
> (Datacenter → Permissions → API Tokens) con permisos VM.Audit +
> VM.PowerMgmt. Pégamelo cuando lo tengas.

> **Operador**: PVEAPIToken=root@pam!claude=abc123def456…

> **LLM**: Lo guardo en el vault, nunca al audit log.

```jsonc
router_add_credential(ref="PROXMOX_PVE1_TOKEN", value="<redacted>")
```

```jsonc
{
  "ref": "PROXMOX_PVE1_TOKEN",
  "stored_at": "<MIMIR_HOME>/secrets/router_vault.md",
  "audited": "ref-only, value never logged"
}
```

## Paso 5 — El plugin se activa

> **LLM**: Refrescando el estado.

```jsonc
router_status()
```

```jsonc
{
  "plugins": [
    {
      "name": "homelab",
      "version": "1.0.0",
      "status": "ok",
      "missing": []
    }
  ],
  "setup_pending": []
}
```

> **LLM**: El plugin de Proxmox está vivo. Ya puedes pedirme cosas
> como *"lista nodos"*, *"arranca la VM 100"*, *"comprueba el storage
> de pve1"*.

## Qué acaba de pasar

El operador nunca:

- Editó un YAML a mano.
- Buscó la ruta donde se guardan credenciales.
- Leyó un tutorial de "configura tu inventario".
- Tuvo que saber la diferencia entre `host`, `service` y
  `credential ref`.

El LLM hizo todo eso *a través de* las meta-tools de Mimir. El
trabajo del framework es hacer esas tools lo bastante precisas
como para que el LLM pueda manejarlas sin sorpresas — y lo bastante
seguras como para que un paso en falso no filtre secretos ni
ejecute código sin consentimiento.

Ese es el centro del diseño. Todo lo demás (capas de seguridad,
schema del manifest, contrato de plugin) existe para hacer esta
conversación posible sin sacrificar ninguna de las dos cosas.

## Ver también

- [`README.es.md`](../../README.es.md) — qué es Mimir y cómo
  instalarlo.
- [`docs/es/INSTALL.md`](INSTALL.md) — caminos de instalación y
  configuración.
- [`docs/es/security-model.md`](security-model.md) — cómo los
  secretos se mantienen fuera del audit log y del transcript del
  cliente.
- [`docs/es/plugin-contract.md`](plugin-contract.md) — qué declara
  un autor de plugin para que el LLM pueda guiar al operador en su
  setup.
- [`examples/echo-plugin/`](../../examples/echo-plugin/) — plantilla
  mínima desde la que empieza un autor.
