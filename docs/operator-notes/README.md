# Operator notes

Estos documentos son **notas del caso de uso del autor** del framework.
Describen cómo `Mimir` se despliega en un homelab concreto
(Proxmox + Linux + UniFi + GPON + serial), qué clientes MCP apuntan a
él (Hermes LXC 302 pve2, Claude Desktop del autor), y los manifests
listos para los plugins que el autor usa a diario.

> **No son parte del contrato público del framework.** Si vienes a
> entender `Mimir` para construir tu propio plugin o
> desplegarlo en tu infra, mira antes:
>
> - `docs/naming-guide.md` — convenciones canónicas de nombres.
> - `docs/plugin-contract.md` — esquema del `plugin.toml`.
> - `docs/inventory-schema.md` — cómo declarar hosts y servicios.
> - `docs/security-model.md` — las 7 capas de seguridad.
> - `examples/echo-plugin/` — plugin mínimo que sirve de plantilla.
>
> Estas `operator-notes/` son útiles como **ejemplo de un despliegue
> real** funcionando, nada más.

## Qué hay aquí

- **`cutover/`** — runbook del autor para migrar sus clientes MCP
  desde el `server.py` legacy al `router.py` nuevo. Incluye los
  manifests `plugin.toml` listos para cada uno de sus plugins
  (proxmox, linux, windows, docker, unifi, uart, gpon).
- **`downstream-issues.md`** — issues concretos que el autor se
  encontró montando MCPs downstream (propagación de env a subprocess
  con `create_proxy`, 401 de UniFi, etc.). Diagnóstico y solución
  documentados para referencia.

## Convención

Cualquier doc que mencione infraestructura concreta del autor
(direcciones IP, hostnames, nombres de nodos, rutas personales,
credenciales), clientes MCP concretos, o decisiones operativas
idiosincráticas, pertenece a `operator-notes/` — nunca a los docs de
raíz.

Si encuentras una mezcla, muévelo aquí.
