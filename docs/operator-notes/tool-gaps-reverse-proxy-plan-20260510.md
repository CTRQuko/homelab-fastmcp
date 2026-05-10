# Tool Gaps detectados — Plan Reverse Proxy + DNS Reset + WG Site-to-Site

*Fecha: 2026-05-10*
*Contexto: planificación de migración arquitectural homelab (3 sedes nginx + WG site-to-site + DNS reset).*
*Propósito: identificar gaps en el inventario de tools mimir-mcp donde actualmente se recurre a `homelab_ssh_run` + Bash directo en lugar de tooling abstraído.*

---

## Resumen

Durante el diseño del plan ANEXO RP (reverse proxy unificado + DNS reset + WG site-to-site) se identificaron **7 áreas funcionales** que actualmente no tienen abstracción en mimir-mcp y se resuelven con `homelab_ssh_run` + comandos shell. Se documenta cada gap con el comando exacto que se ejecuta para que el equipo de mimir-mcp decida si justifica añadir tool nativa.

| # | Área | Severidad | Frecuencia uso | ¿Justifica tool? |
|---|---|---|---|---|
| 1 | nginx write file (escritura) | ~~Alta~~ → **RESUELTO v0.3.0** — `nginx-ui-ops_nginx_write_file` ya implementado, gated por `NGINXUI_ALLOW_MUTATIONS` | Diaria | ~~Sí~~ → **Solo activar mutations** |
| 2 | OpenWrt UCI commands | Baja — **solo router Munilla** (Logroño cubierto por nuevas tools `unifi_*`) | Ocasional | Sí, valor bajo (1 router) |
| 3 | WireGuard keygen + conf | Baja — instalación inicial | Una vez por peer | No, gen cmds estándar |
| 4 | Cloudflare DNS API | Media — DNS records + ACME DNS-01 | Frecuente (DNS records) | **Sí, alto valor** |
| 5 | Hetzner Cloud API | Media — firewall + servers + snapshots | Ocasional | **Sí, valor medio** |
| 6 | Pi-hole REST API | Alta — gestión DNS records por servicio | Diaria | **Sí, alto valor** |
| 7 | AdGuard REST API | Alta — gestión rewrites + filters | Diaria | **Sí, alto valor** |

---

## Gap 1 — nginx escribir archivos config

> **✅ RESUELTO en nginx-ui-ops v0.3.0 (2026-05-06)**
>
> `nginx-ui-ops_nginx_write_file` ya está implementado en `nginx_ui_ops/tools/ops.py`
> con backup atómico + rollback automático si `nginx -t` falla. Tests 15/15 passing.
>
> **No es un gap — es un flag a activar**:
>
> ```python
> router_add_credential('NGINXUI_ALLOW_MUTATIONS', 'true')
> # + restart Claude Code → 4 mutating tools quedan disponibles:
> #   nginx_write_file, nginx_full_restart, nginx_reopen_logs, nginx_quit
> ```
>
> El plugin ya expone `cert_issue`, `cert_domains_update`, `cert_deploy_files`,
> `nginx_reload`, `nginx_write_file`, `nginx_full_restart`, `nginx_reopen_logs`,
> `nginx_quit` — todos gated por el mismo flag de mutations.
>
> El resto de la sección queda como referencia histórica del workflow shell-only
> que se sustituye con la activación.

**Tools existentes (lectura/validación)**:
- `nginx-ui-ops_nginx_dump_config()` — `nginx -T` completo
- `nginx-ui-ops_nginx_read_file(path)` — leer archivo individual
- `nginx-ui-ops_nginx_test()` — validar config actual
- `nginx-ui-ops_nginx_test_with_diff(target_path, proposed_content)` — **valida proposed_content sin tocar prod, devuelve diff**

**Lo que faltaba** (cerrado en v0.3.0): contraparte `nginx_write_file(path, content)` para aplicar el `proposed_content` validado.

**Workflow actual**:
```python
# Paso 1: validar (tool MCP existente)
result = nginx_test_with_diff(
    target_path="/etc/nginx/sites-available/unifi.conf",
    proposed_content="server { listen 443 ssl; server_name unifi.casaredes.cc; ... }"
)
# result = {ok: true, diff: "..."}

# Paso 2: escribir via SSH heredoc (workaround)
homelab_ssh_run(
    node="lxc-nginx-l1",
    sudo=true,
    command="""tee /etc/nginx/sites-available/unifi.conf > /dev/null <<'NGINXEOF'
server {
    listen 443 ssl;
    server_name unifi.casaredes.cc;
    ssl_certificate /etc/nginx/ssl/wildcard/fullchain.cer;
    ssl_certificate_key /etc/nginx/ssl/wildcard/private.key;
    location / {
        proxy_pass https://10.0.1.10:11443;
    }
}
NGINXEOF"""
)

# Paso 3: enable site
homelab_ssh_run(
    node="lxc-nginx-l1",
    sudo=true,
    command="ln -sf /etc/nginx/sites-available/unifi.conf /etc/nginx/sites-enabled/unifi.conf"
)

# Paso 4: reload
homelab_ssh_run(node="lxc-nginx-l1", sudo=true, command="systemctl reload nginx")
```

**Tool propuesta**:
```python
nginx_write_file(
    path: str,                    # /etc/nginx/sites-available/X.conf
    content: str,
    enable_site: bool = False,    # crea symlink en sites-enabled/
    reload: bool = False,         # systemctl reload tras escribir
    validate_first: bool = True   # ejecuta nginx_test antes (default seguro)
) -> {ok, written, enabled, reloaded, error}
```

**Justificación**: cierra el ciclo lectura→validación→escritura→reload sin que el caller tenga que componer heredoc. Actualmente la doc de `nginx_test_with_diff` ya menciona `nginx_write_file` como tool complementaria pero no está expuesta.

---

## Gap 2 — OpenWrt UCI (scope reducido a Munilla)

> **Re-scoped 2026-05-10**: tras desplegar `unifi 1.0.0` en mimir (74 tools UniFi),
> el lado **Logroño** queda cubierto:
> - `unifi_create_port_forward` / `update` / `delete` — port forwarding UDM Logroño
> - `unifi_create_dhcp_reservation` / `update` / `remove` — DHCP UDM
> - `unifi_create_firewall_rule` / `policy` / `zone` — firewall UDM
>
> **Sólo queda OpenWrt Munilla** (TP-Link Archer C7 v2, independiente del controller
> UniFi). 1 solo router → severidad **Baja**, prioridad **P3**.

**Tools existentes**: ninguna específica para OpenWrt. Para UDM/UDR Logroño,
ver tools `unifi_*` (plugin unifi v1.0.0 desplegado 2026-05-10).

**Workflow actual** (Munilla únicamente): SSH directo via alias `laposada` (key añadida) + UCI commands.

```python
# Port forwarding UDP 51820
homelab_ssh_run(
    node="laposada",
    sudo=False,  # OpenWrt: root directo, sin user claude
    command="""
uci set firewall.wg_pfwd=redirect
uci set firewall.wg_pfwd.name='WireGuard site-to-site'
uci set firewall.wg_pfwd.src='wan'
uci set firewall.wg_pfwd.proto='udp'
uci set firewall.wg_pfwd.src_dport='51820'
uci set firewall.wg_pfwd.dest='lan'
uci set firewall.wg_pfwd.dest_ip='192.168.2.X'
uci set firewall.wg_pfwd.dest_port='51820'
uci commit firewall
service firewall reload
"""
)

# Static route
homelab_ssh_run(
    node="laposada",
    sudo=False,
    command="""
uci add network route
uci set network.@route[-1].interface='lan'
uci set network.@route[-1].target='10.0.1.0/24'
uci set network.@route[-1].gateway='192.168.2.X'
uci set network.@route[-1].metric='10'
uci commit network
service network reload
"""
)
```

**Tool propuesta**:
```python
openwrt_port_forward(node, name, proto, src_port, dest_ip, dest_port)
openwrt_route_add(node, target, gateway, metric, interface)
openwrt_uci_get(node, path)        # uci show / get
openwrt_uci_commit(node, package)  # commit + service reload
```

**Justificación**: OpenWrt está desplegado en Munilla y en el futuro otros routers. Sin tool, cada cambio routing/firewall requiere construir UCI commands a mano con riesgo de typo. Tool encapsularia los patrones más comunes (port forward, route, dhcp lease).

---

## Gap 3 — WireGuard keygen + conf

**Tools existentes**: ninguna.

**Workflow actual**: comandos `wg` estándar via SSH.

```python
# Gen keys EN el peer (privada nunca sale)
result = homelab_ssh_run(
    node="lxc-wg-l1",
    sudo=True,
    command="wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key && chmod 600 /etc/wireguard/private.key && cat /etc/wireguard/public.key"
)
# result.stdout = public key

# Componer wg0.conf
homelab_ssh_run(
    node="lxc-wg-l1",
    sudo=True,
    command="""tee /etc/wireguard/wg0.conf > /dev/null <<WGEOF
[Interface]
PrivateKey = $(cat /etc/wireguard/private.key)
Address = 10.255.0.1/24
ListenPort = 51820

[Peer]
PublicKey = <pubkey-wg-mun>
Endpoint = <pub-ip-mun>:51820
AllowedIPs = 10.255.0.2/32, 192.168.2.0/24
WGEOF
chmod 600 /etc/wireguard/wg0.conf"""
)

# Iniciar (Alpine OpenRC)
homelab_ssh_run(
    node="lxc-wg-l1",
    sudo=True,
    command="rc-update add wg-quick default && rc-service wg-quick start"
)
```

**Tool potencial (baja prioridad)**:
```python
wireguard_genkey(node)       # genera + persiste, devuelve pubkey
wireguard_apply_config(node, peers: list, address, listen_port)  # compone wg0.conf y reinicia
wireguard_status(node)       # wg show
```

**Justificación NO añadir**: workflow es one-shot por peer (3 veces, una vez en la vida del homelab). Complejidad de tool > complejidad workaround. **Recomendación: NO añadir** salvo que el repo opere muchos despliegues WG nuevos.

---

## Gap 4 — Cloudflare DNS API

**Tools existentes**: ninguna específica.

**Workflow actual**: curl con `CF_API_TOKEN`.

```bash
TOKEN=$(grep -oP 'API_TOKEN=\K\S+' /c/homelab/.config/secrets/cloudflare.md)
ZONE_ID="1c37d2535befd8435fbf96dfbf07d367"

# Listar records
curl -sf -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" | jq

# Add record
curl -sfX POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"type":"A","name":"foo.casaredes.cc","content":"100.69.126.35","proxied":false,"ttl":300}' \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records"

# Update / Delete idem con PATCH/DELETE + record_id

# ACME DNS-01: lo gestiona acme.sh internamente con CF_Token env
ssh hetzner-claude "export CF_Token='$TOKEN' && acme.sh --issue --dns dns_cf -d '*.casaredes.cc'"
```

**Tools propuestas**:
```python
cloudflare_dns_list(zone)
cloudflare_dns_create(zone, type, name, content, proxied=False, ttl=300)
cloudflare_dns_update(zone, record_id, ...)
cloudflare_dns_delete(zone, record_id)
cloudflare_acme_issue(zone, hostname, output_path)  # wrapper acme.sh
```

**Justificación**: la zona `casaredes.cc` se modifica con cada cambio arquitectural (este plan, plan v4 DNS, futuro). Token guardado en `.config/secrets/cloudflare.md`. Tool nativa eliminaría riesgo de:
- Modificar record incorrecto (humano se equivoca con record_id)
- Olvidar `proxied: false` (ADR-0002 obligatorio en este homelab)
- Logs sensibles con token en stdout

**Validaciones built-in**:
- `cloudflare_dns_create(... proxied=True)` con dominio en lista negra → reject
- Confirma zone == casaredes.cc antes de operar

---

## Gap 5 — Hetzner Cloud API

**Tools existentes**: ninguna específica.

**Workflow actual**: curl con `HETZNER_API_TOKEN` (en `.config/secrets/hetzner.md`).

```bash
TOKEN=$(grep -oP 'HETZNER_API_TOKEN=\K\S+' /c/homelab/.config/secrets/hetzner.md)

# List firewalls
curl -sf -H "Authorization: Bearer $TOKEN" "https://api.hetzner.cloud/v1/firewalls" | \
  jq '.firewalls[] | {id, name, applied_to: [.applied_to[]]}'

# Add rule (set_rules REEMPLAZA, hay que enviar TODAS)
CURRENT=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "https://api.hetzner.cloud/v1/firewalls/$FW_ID" | jq '.firewall.rules')

NEW_RULES=$(echo "$CURRENT" | jq '. + [{
  "direction": "in",
  "protocol": "udp",
  "port": "51820",
  "source_ips": ["0.0.0.0/0", "::/0"],
  "description": "WireGuard"
}]')

curl -sfX POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"rules\": $NEW_RULES}" \
  "https://api.hetzner.cloud/v1/firewalls/$FW_ID/actions/set_rules"
```

**Riesgo conocido**: `set_rules` action sobreescribe el conjunto entero. Si construyes el array final mal, pierdes reglas existentes (incluido el TS UDP 41641 que es vital). Mitigación actual: backup en `/tmp` antes.

**Tools propuestas**:
```python
hetzner_firewall_list()
hetzner_firewall_get(fw_id)
hetzner_firewall_add_rule(fw_id, direction, protocol, port, source_ips, description)
# ↑ encapsula el patrón "leer current → append → set_rules" de forma atómica con backup
hetzner_firewall_remove_rule(fw_id, rule_index_or_match)
hetzner_server_list()
hetzner_server_snapshot_create(server_id, description)
```

**Justificación**: pattern `read-modify-write` con preservación de reglas existentes es trivial bug source. Encapsular en tool elimina riesgo de borrar reglas críticas accidentalmente.

---

## Gap 6 — Pi-hole REST API (v6)

**Tools existentes**: ninguna específica.

**Workflow actual**: curl con auth SID.

```bash
PASS=$(grep -oP 'password:\s*\K\S+' .config/secrets/pihole.md)

# Login
SID=$(curl -sk -X POST -d "{\"password\":\"$PASS\"}" \
  "https://100.99.189.118/api/auth" | jq -r '.session.sid')

# Get config
curl -sk -H "X-FTL-SID: $SID" "https://100.99.189.118/api/config" | jq

# Update config (whole config replace, careful)
curl -sk -X PATCH -H "X-FTL-SID: $SID" -H "Content-Type: application/json" \
  -d '{"config":{"dns":{"hosts":["10.0.1.40 *.casaredes.cc"]}}}' \
  "https://100.99.189.118/api/config"

# Reload
curl -sk -X POST -H "X-FTL-SID: $SID" \
  "https://100.99.189.118/api/action/reloaddns"

# Logout
curl -sk -X DELETE -H "X-FTL-SID: $SID" \
  "https://100.99.189.118/api/auth"
```

**Riesgo conocido**:
- API v6 responses cambian entre minor versions
- SID en logs si curl falla en stderr
- `/api/config` PATCH reemplaza esa sección entera — perder hosts existentes posible

**Fallback edición directa** (cuando API falla):
```python
homelab_ssh_run(
    node="pve2",
    sudo=True,
    command="pct exec 101 -- /usr/local/bin/claude-wrapper tee /etc/pihole/pihole.toml < /tmp/pihole.toml.new"
)
```
Pero requiere wrapper profile permita `tee /etc/pihole/*` (NO suele estar whitelisted).

**Tools propuestas**:
```python
pihole_login(host)  # devuelve SID, persiste en memoria de tool
pihole_get_config(host, section=None)
pihole_dns_hosts_list(host)
pihole_dns_hosts_add(host, ip, hostname)
pihole_dns_hosts_remove(host, hostname)
pihole_reload_dns(host)
# Auto-logout al final de cada call (clean SID)
```

**Justificación**: Pi-hole se gestiona ×3 (TS L1, VPS, Mun). Sin tool, cada cambio = 4 curl calls (login, op, reload, logout) ×3. Tool encapsula auth + mantiene catálogos sincronizados.

---

## Gap 7 — AdGuard REST API

**Tools existentes**: ninguna específica.

**Workflow actual**: curl Basic Auth.

```bash
USER=$(grep -oP 'user:\s*\K\S+' .config/secrets/adguard.md)
PASS=$(grep -oP 'password:\s*\K\S+' .config/secrets/adguard.md)

# List rewrites
curl -sk -u "$USER:$PASS" "http://10.0.1.14/control/rewrite/list" | jq

# Add rewrite
curl -sk -u "$USER:$PASS" -X POST -H "Content-Type: application/json" \
  -d '{"domain":"*.casaredes.cc","answer":"10.0.1.40"}' \
  "http://10.0.1.14/control/rewrite/add"

# Delete rewrite (entry completo en body)
curl -sk -u "$USER:$PASS" -X POST -H "Content-Type: application/json" \
  -d '{"domain":"old.casaredes.cc","answer":"10.0.1.40"}' \
  "http://10.0.1.14/control/rewrite/delete"

# Bulk delete (loop)
curl -sk -u "$USER:$PASS" "http://10.0.1.14/control/rewrite/list" | \
  jq -r '.[] | select(.domain != "*.casaredes.cc") | @json' | \
  while read entry; do
    curl -sk -u "$USER:$PASS" -X POST -H "Content-Type: application/json" \
      -d "$entry" "http://10.0.1.14/control/rewrite/delete"
  done
```

**Riesgo conocido**: bulk delete en loop es transaccionalmente débil — si falla a mitad queda config inconsistente. Sin idempotencia.

**Tools propuestas**:
```python
adguard_rewrites_list(host)
adguard_rewrites_add(host, domain, answer)
adguard_rewrites_remove(host, domain)
adguard_rewrites_set(host, rewrites: list)  # transaction-like: stage + apply
adguard_filter_status(host)
adguard_dhcp_leases(host)
```

**Justificación**: AdGuard se gestiona ×2 (L1 + L2 cuando esté operativo). Bulk operations frecuentes (limpiar rewrites legacy en este plan, sincronizar pares Logroño/Munilla). Tool con `set` atómico evita drift.

---

## Recomendaciones priorizadas

| Prioridad | Tool | Razón |
|---|---|---|
| ~~**P0**~~ ✅ | ~~`nginx_write_file`~~ | **RESUELTO**: ya implementado en nginx-ui-ops v0.3.0. Solo activar `NGINXUI_ALLOW_MUTATIONS=true` |
| **P0** | `cloudflare_dns_*` | Operaciones DNS frecuentes. Token disponible. Validaciones built-in (proxied:false) reducen errores |
| **P0** | `pihole_*` | 3 instancias gestionadas (TS L1 + VPS + Mun). Tool elimina boilerplate auth + previene drift |
| **P0** | `adguard_*` | 2 instancias. Tool con `set` atómico crítico para bulk ops |
| **P2** | `hetzner_firewall_*` | Bug-prone read-modify-write. Tool con backup automático elimina riesgo |
| **P3** | `openwrt_uci_*` | Solo Munilla tras unifi plugin (Logroño cubierto). Patterns comunes (port forward, route). Baja urgencia |
| **NO** | `wireguard_*` | One-shot setup. Comandos estándar bastan. |

### Tools recientes que tapan gaps (ref. para no duplicar trabajo)

Plugin `nginx-ui-ops 0.3.0` (gated por `NGINXUI_ALLOW_MUTATIONS=true`):
- `nginx_write_file`, `nginx_full_restart`, `nginx_reopen_logs`, `nginx_quit`
- `cert_issue`, `cert_domains_update`, `cert_deploy_files`, `nginx_reload`

Plugin `unifi 1.0.0` (74 tools, modo local API_KEY):
- Port forwards: `create_port_forward`, `update_port_forward`, `delete_port_forward`, `list_port_forwards`
- DHCP: `create_dhcp_reservation`, `update_dhcp_reservation`, `remove_dhcp_reservation`, `list_dhcp_reservations`
- Firewall: `create_firewall_rule`, `*_policy`, `*_zone`, `*_group`
- Devices: `list_devices_by_type`, `restart_device`, `upgrade_device`, `locate_device`
- Clients: `list_active_clients`, `block_client`, `unblock_client`, `reconnect_client`
- WLANs/VLANs: `list_wlans`, `create_wlan`, `update_wlan`, `list_vlans`
- Backups: `trigger_backup`, `list_backups`, `restore_backup`

Antes de añadir tool nueva al inventario mimir: comprobar si existe en
`mcp__mimir-mcp__unifi_*` o en las mutating de `nginx-ui-ops`.

## Notas adicionales

- Todos los tokens/passwords mencionados viven en `C:/homelab/.config/secrets/*.md` — fuera de git (`.gitignore` global + pre-commit hook).
- El patrón establecido en mimir-mcp es leer secrets via env var (`CLAUDE_SUDO_KEY_FILE` para sudo password). Tools nuevas deberían seguir este patrón: `CLOUDFLARE_TOKEN_FILE`, `HETZNER_TOKEN_FILE`, etc.
- Para tools que tocan estado (POST/PATCH/DELETE): considerar parámetro `confirm=True` (patrón ya usado en `homelab_restart_vm` etc.) para evitar ejecuciones accidentales.

---

## Referencias

- Plan que motivó el reporte: `~/.claude/plans/ajustandose-a-mi-red-glimmering-simon.md` § ANEXO RP
- Inventario tools verificado vía ToolSearch 2026-05-10
- Secrets paths: `C:/homelab/.config/secrets/` (BitLocker, no git)
