# Schema del inventory

🇬🇧 [Read in English](../inventory-schema.md)

El framework **no tiene conocimiento built-in** de la infraestructura
del usuario. Hosts y servicios se declaran en ficheros
`inventory/*.yaml`; los plugins le piden al router qué existe, nunca
hardcodean direcciones ni credenciales. Esta es la única frontera
donde un usuario le enseña al framework cómo es su mundo.

## Ficheros

- `inventory/hosts.yaml.example` — plantilla, viene en el repo.
  Solo direcciones placeholder neutras de RFC 5737.
- `inventory/hosts.yaml` — la pone el usuario. **Gitignored.**
- `inventory/services.yaml.example` — plantilla.
- `inventory/services.yaml` — la pone el usuario. **Gitignored.**

El router lee lo que haya en `inventory_dir` (configurable en
`router.toml`). Si ninguno existe, el inventory está vacío — los
plugins que no tengan requirements de host siguen funcionando; los
que necesiten hosts se quedan en `pending_setup`.

## Hosts

```yaml
hosts:
  - name: myhost1                       # obligatorio, único
    type: linux                         # linux | windows | macos | proxmox | …
    address: 192.0.2.10                 # IPv4/IPv6 o hostname
    port: 22                            # opcional, default específico del plugin
    auth:
      method: ssh_key                   # ssh_key | password | api_token | jwt | …
      credential_ref: SSH_KEY_MYHOST1   # referencia, NO un valor
    tags: [prod, database]              # opcional, usado por requires.hosts.tag
```

### Campos obligatorios

- `name` — identificador único usado por servicios (`host_ref`) y
  por humanos.
- `type` — string libre. La convención es minúsculas sin espacios.
  Los plugins declaran qué valores de `type` aceptan.
- `address` — el router no valida si es alcanzable, solo el
  formato.

### Campos opcionales

- `port` — numérico, almacenado tal cual.
- `auth.credential_ref` — nombre de una env var o entrada del
  vault. El valor crudo **nunca** aparece en el YAML.
- `tags` — lista de strings. `[[requires.hosts]]` puede filtrar
  por tag.

## Services

```yaml
services:
  - name: mycontroller                  # obligatorio, único
    type: unifi                         # string libre
    host_ref: myhost1                   # debe casar con el `name` de un host
    port: 11443                         # opcional
    auth:
      method: jwt
      credential_ref: UNIFI_MYCONTROLLER_TOKEN
```

`host_ref` se valida al cargar: un servicio apuntando a un host
inexistente lanza `InventoryError`, que el router muestra y se
niega a arrancar en vez de dropear el servicio en silencio.

## Escrituras vía bootstrap tools

El LLM no debería editar YAML directamente. Hay cuatro rutas de
escritura, todas pasando por `core.bootstrap`:

- `router_add_host(name, type, address, port?, credential_ref?, auth_method?, tags?)`
- `router_add_service(name, type, host_ref, port?, credential_ref?, auth_method?)`
- `router_add_credential(ref, value)` — la credencial va al vault
  scoped (`$MIMIR_HOME/secrets/router_vault.md`), nunca al YAML.
- Cada escritura dispara `state.refresh()` para que el siguiente
  `router_status()` refleje el estado nuevo — el LLM no necesita
  reiniciar nada.

## API del plugin — solo lectura

Los plugins consumen el inventory vía `core.inventory.Inventory`:

```python
inv.get_hosts(type="proxmox", tag="prod")   # lista filtrada de Host
inv.get_services(host_ref="myhost1")
inv.get_credentials("PROXMOX_*_TOKEN", plugin_ctx)  # gateado por manifest
```

Los plugins nunca abren el YAML por su cuenta; nunca ven
`os.environ`; nunca ven el fichero del vault. Las tres cosas pasan
por el router para que el audit cubra todo y la imposición del
scope quede en un solo sitio.

## Qué **no** va en el inventory

- Valores de credenciales. Solo refs.
- Config específica del plugin más allá de
  `name/type/address/port/auth/tags/host_ref`. Un plugin que
  necesite config extra lee su propio fichero bajo su dir de
  plugin, o pide al usuario via su meta-tool `setup_<plugin>()`
  que añada una credencial.
- La config propia del framework (`router.toml`). Eso vive bajo
  `config/`.
