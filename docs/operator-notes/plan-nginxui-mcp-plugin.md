# Plan — Plugin mimir `nginx-ui-ops` (publicable, multi-backend)

**Estado**: v3 — backend-agnóstico desde día 1. Publicable como plugin
upstream para la comunidad mimir.
**Fecha**: 2026-05-06.
**Ámbito**: nuevo plugin `nginx-ui-ops` en `mimir-mcp/plugins/`,
diseñado para extracción posterior a `CTRQuko/nginx-ui-ops-mcp`.

---

## Context

`nginx-ui` (https://nginxui.com) es un panel web para gestionar nginx
con MCP server integrado. El MCP nativo cubre vhost mgmt pero **no
cubre cert management** (emitir certs vía acme.sh, deploy a disk,
actualizar la DB SQLite interna de nginx-ui, restart servicio).

El plugin `nginx-ui-ops` encapsula ese ciclo en tools MCP. Diseñado
desde el principio para ser:

1. **Portable** — funciona en cualquier setup que tenga nginx-ui
   accesible (no asume Proxmox/LXC/wrapper específico).
2. **Anonimizado** — cero datos del operador hardcoded (no hostnames,
   IPs, dominios, credenciales literales).
3. **Publicable** — apto para distribución vía mimir-mcp registry o
   repo upstream `CTRQuko/nginx-ui-ops-mcp`.

El operador no es el único humano con un nginx-ui en LAN que quiere
gestión de certs vía LLM. La inversión en abstracción ahora evita
tener que reescribirlo después cuando aparezcan más casos.

---

## Decisiones cerradas (operador confirmó)

1. ✅ **Backend abstracto desde día 1** (Opción B). Plugin publishable
   desde el primer release, no privado.
2. ✅ **Anonimización completa** — cero referencias a `casaredes.cc`,
   IPs `10.0.1.x`, LXC IDs concretos, paths del operador. Todo via
   config/env vars.
3. ✅ **Asumir keys actuales nginx-ui inválidas** durante el diseño;
   operador rotará al final, antes de implementar.

---

## Arquitectura

### Capas

```
┌─────────────────────────────────────────────┐
│ Tools MCP (8) — interfaz pública            │
│ cert_list, cert_get, cert_domains_update,   │
│ cert_issue, cert_deploy_files, nginx_test,  │
│ nginx_reload, nginx_cert_validate           │
└──────────────────┬──────────────────────────┘
                   │ (agnóstico al transport)
                   ▼
┌─────────────────────────────────────────────┐
│ Backend ABC (NginxUIBackend)                │
│ run_cmd, push_file, read_file, query_db,    │
│ acme_issue, validate_cert_remote            │
└──────────────────┬──────────────────────────┘
                   │ (selector via NGINXUI_BACKEND env var)
        ┌──────────┴──────────┐
        ▼                     ▼
┌─────────────────┐  ┌─────────────────┐
│ WrapperLXCBack. │  │ DirectSSHBack.  │
│ (Proxmox+LXC+   │  │ (host SSH +     │
│  claude-wrapper)│  │  sudo NOPASSWD) │
└─────────────────┘  └─────────────────┘
```

Más backends futuros: `DockerExecBackend`, `LocalBackend`,
`KubernetesBackend`. Cada uno se añade sin tocar las tools públicas.

### Estructura de archivos

```
plugins/nginx-ui-ops/
├── plugin.toml
├── pyproject.toml
├── README.md                    # Cómo instalar + qué backend elegir
├── nginx_ui_ops/
│   ├── __init__.py
│   ├── server.py                # MCP entry: registra 8 tools, carga backend
│   ├── tools/
│   │   ├── certs.py             # cert_list, cert_get, cert_domains_update,
│   │   │                        # cert_issue, cert_deploy_files
│   │   ├── nginx.py             # nginx_test, nginx_reload
│   │   └── validate.py          # nginx_cert_validate (openssl, no backend)
│   ├── backends/
│   │   ├── base.py              # ABC NginxUIBackend
│   │   ├── wrapper_lxc.py       # implementación operador
│   │   ├── direct_ssh.py        # implementación genérica
│   │   └── factory.py           # select backend por env var
│   └── models.py                # Pydantic: CertInfo, ValidationResult, etc.
└── tests/
    ├── test_certs.py            # tools con mock backend
    ├── test_nginx.py
    ├── test_validate.py
    ├── backends/
    │   ├── test_wrapper_lxc.py  # con mock subprocess (los 4 gotchas)
    │   └── test_direct_ssh.py   # con mock subprocess
    └── test_factory.py          # selector por env var
```

---

## Backend ABC

```python
# nginx_ui_ops/backends/base.py
from abc import ABC, abstractmethod
from pathlib import Path

class NginxUIBackend(ABC):
    """Transport interface for ops on the nginx-ui host.

    Implementations encapsulate HOW commands reach the box: SSH+wrapper
    in a Proxmox LXC, direct SSH, docker exec, local subprocess, etc.
    Tool code never sees these details.
    """

    @abstractmethod
    def run_cmd(self, argv: list[str], *, sudo: bool = False) -> CompletedProcess:
        """Run a command on the nginx-ui host. Capture stdout/stderr/rc.

        ``sudo`` indicates the command needs elevated privileges. The
        backend MUST handle escalation in a way that doesn't mix
        password with stdin (gotcha #2 of the operator's log).
        """

    @abstractmethod
    def push_file(self, content: bytes, remote_path: str, *, mode: int = 0o644) -> None:
        """Place ``content`` at ``remote_path`` on the nginx-ui host.

        The implementation MUST handle CRLF normalization for binary-safe
        files (configs come from Windows operators frequently — gotcha #3).
        Caller passes raw bytes; backend decides whether to strip CR.
        """

    @abstractmethod
    def read_file(self, remote_path: str) -> bytes:
        """Fetch raw bytes of a file on the nginx-ui host."""

    @abstractmethod
    def query_db(self, db_path: str, sql: str, *, params: tuple = ()) -> list[dict]:
        """Run a SQL query against an SQLite DB on the host.

        Implementations MUST handle parameterization safely — SQL with
        quotes / placeholders that the transport layer might mangle
        (gotcha #4: wrapper's eval destroys quotes; mitigation is
        writing the SQL to a tempfile and redirecting stdin).
        """

    @abstractmethod
    def acme_issue(self,
                   domains: list[str],
                   *,
                   key_type: str,
                   dns_provider: str,
                   provider_env: dict[str, str]) -> dict:
        """Run acme.sh --issue and return paths to the new cert+key.

        ``provider_env`` are env vars the acme.sh DNS provider needs
        (e.g. CF_Token, CF_Zone_ID for cloudflare). Backend forwards
        them to the subprocess scope only.

        gotcha #1 (acme.sh CF zone lookup with restrictive token) is
        handled by the caller passing the explicit Zone_ID via
        ``provider_env``. Backend does not assume any provider.
        """
```

### `WrapperLXCBackend` (operador)

Encapsula los 4 gotchas del log:
- **#1** acme.sh CF zone lookup → caller pasa CF_Zone_ID explícito vía `provider_env`.
- **#2** sudo+stdin mezcla password → `run_cmd(sudo=True)` usa NOPASSWD path o pipe separado.
- **#3** CRLF Windows → `push_file` siempre normaliza con `tr -d '\r'` antes de `pct push`.
- **#4** wrapper eval destruye SQL quotes → `query_db` escribe SQL a tempfile + redirección stdin.

Config (env vars del plugin):
```
NGINXUI_BACKEND=wrapper-lxc          # selector
NGINXUI_PVE_SSH_ALIAS=<alias>        # alias SSH del nodo Proxmox
NGINXUI_LXC_ID=<id>                  # ID del LXC con nginx-ui
NGINXUI_WRAPPER_PATH=/usr/local/bin/claude-wrapper  # opcional, default
NGINXUI_SUDO_METHOD=nopasswd|password # default nopasswd; password lee secret
NGINXUI_SUDO_PASSWORD_REF=<ruta>     # archivo con password si SUDO_METHOD=password
```

### `DirectSSHBackend` (genérico)

Para usuarios que tienen nginx-ui en host SSH-able (bare metal, VM,
contenedor con SSH expuesto). No necesita Proxmox ni LXC.

Config:
```
NGINXUI_BACKEND=direct-ssh
NGINXUI_HOST=<host>                  # SSH alias o IP
NGINXUI_SSH_USER=<user>              # default `claude` o `root`
NGINXUI_SUDO_METHOD=nopasswd|password|none  # `none` si es root
```

Asume estructura nginx-ui estándar (paths fijos según docs nginx-ui).
Los 4 gotchas del log aplican parcialmente: gotcha #2 (sudo+stdin)
sigue, los otros 3 son específicos de wrapper LXC.

### `factory.py` — selector

```python
def get_backend() -> NginxUIBackend:
    name = os.environ.get("NGINXUI_BACKEND", "").strip().lower()
    if name == "wrapper-lxc":
        return WrapperLXCBackend.from_env()
    if name == "direct-ssh":
        return DirectSSHBackend.from_env()
    raise PluginConfigError(
        f"NGINXUI_BACKEND={name!r} unknown. "
        f"Supported: wrapper-lxc, direct-ssh."
    )
```

Tests: `test_factory.py` valida que cada backend se instancia desde
sus env vars + falla loud sin env var.

---

## Tools (8 públicas, agnósticas al backend)

Mismo set que el log de la sesión, ahora a través de `backend`:

```python
# tools/certs.py — implementación esquemática

def cert_list(deleted: bool = False) -> list[dict]:
    """SELECT id, name, domains[], paths, auto_cert, challenge_method,
    dns_credential_id, key_type FROM certs."""
    backend = get_backend()
    where = "" if deleted else " WHERE deleted_at IS NULL"
    rows = backend.query_db(
        os.environ.get("NGINXUI_DB_PATH", "/usr/local/etc/nginx-ui/database.db"),
        f"SELECT id, name, domains, ssl_certificate_path, "
        f"ssl_certificate_key_path, auto_cert, challenge_method, "
        f"dns_credential_id, key_type FROM certs{where};",
    )
    return [_normalize_cert(r) for r in rows]


def cert_issue(domains: list[str],
               key_type: str = "P256",
               dns_provider: str = "dns_cf") -> dict:
    """Emite cert vía acme.sh. NO hace deploy automático.

    DNS provider env vars vienen de credential_refs configurados:
      dns_cf  → CF_API_TOKEN, CF_ZONE_ID
      dns_aws → AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
      dns_do  → DO_API_TOKEN
      ...     → ver acme.sh docs

    Caller debe haber poblado los env vars que ese provider necesita.
    """
    backend = get_backend()
    provider_env = _collect_provider_env(dns_provider)  # del os.environ
    return backend.acme_issue(
        domains, key_type=key_type,
        dns_provider=dns_provider,
        provider_env=provider_env,
    )

# ... resto de tools idem
```

Las 8 tools (sin cambios de signature respecto a v2):

```
cert_list(deleted=False)
cert_get(cert_id)
cert_domains_update(cert_id, domains)
cert_issue(domains, key_type="P256", dns_provider="dns_cf")
cert_deploy_files(cert_id)
nginx_test()
nginx_reload()
nginx_cert_validate(hostname, port=443)
```

`nginx_cert_validate` NO usa backend — usa `openssl s_client` desde
el host del operador (es validación externa, no requiere acceso al
servidor).

---

## Anonimización — checklist

Cosas a NO hardcodear en el código del plugin (todo via config/env):

```
✗ casaredes.cc                  → caller pasa domains
✗ 10.0.1.40                     → backend lo resuelve por SSH alias
✗ LXC ID 104                    → NGINXUI_LXC_ID env
✗ pve2 hostname                 → NGINXUI_PVE_SSH_ALIAS env
✗ /home/claude/.acme.sh         → NGINXUI_ACME_HOME env (default ~/.acme.sh)
✗ "claude" user                 → NGINXUI_SSH_USER env
✗ /etc/nginx/ssl/<wildcard>_... → derivado de la DB nginx-ui (nginx-ui
                                  decide los paths, no el plugin)
✗ /usr/local/etc/nginx-ui/database.db → NGINXUI_DB_PATH env
                                          (default = path documentado nginx-ui)
✗ Cloudflare token              → CF_API_TOKEN (genérico, no del operador)
✗ Cloudflare Zone ID            → CF_ZONE_ID
```

Tests específicos: `test_no_hardcoded_strings.py` que grep el código
buscando IPs/dominios sospechosos. Falla CI si alguien introduce uno.

---

## Credenciales (refs simbólicas)

Plugin manifest:

```toml
# plugin.toml [security]
credential_refs = [
  # Backend selector (uno de los siguientes)
  "NGINXUI_BACKEND",                  # "wrapper-lxc" | "direct-ssh" | ...

  # Common
  "NGINXUI_DB_PATH",                  # opcional, default conocido
  "NGINXUI_ACME_HOME",                # opcional, default ~/.acme.sh

  # Backend wrapper-lxc
  "NGINXUI_PVE_SSH_ALIAS",
  "NGINXUI_LXC_ID",
  "NGINXUI_WRAPPER_PATH",             # opcional
  "NGINXUI_SUDO_METHOD",              # opcional, default nopasswd
  "NGINXUI_SUDO_PASSWORD_REF",        # opcional si SUDO_METHOD=password

  # Backend direct-ssh
  "NGINXUI_HOST",
  "NGINXUI_SSH_USER",

  # acme.sh DNS providers (subset; añadir según uso)
  "CF_API_TOKEN", "CF_ZONE_ID",       # cloudflare
  "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION",  # route53
  "DO_API_TOKEN",                     # digitalocean
  # ... otros providers acme.sh según necesidad
]
```

El operador completa los valores en `secrets/` con su convención. El
plugin no asume valores por defecto para nada secreto.

---

## Migration steps (cuando OK final)

```
Pre-step:  Operador rota 4 secrets nginx-ui (node_secret, JWT, crypto,
           CF_API_TOKEN), persistirlos en secrets/nginxui.md y
           secrets/cloudflare.md.                              (~10 min)

Step 1:    Crear estructura del plugin (plugin.toml, pyproject,
           README.md, server.py skeleton, models.py).          (~30 min)

Step 2:    Definir NginxUIBackend ABC en backends/base.py + tests
           del contrato (test_backend_protocol).               (~30 min)

Step 3a:   Implementar WrapperLXCBackend con los 4 gotchas
           encapsulados + tests con mocks subprocess.          (~75 min)

Step 3b:   Implementar DirectSSHBackend (más simple — gotcha #2 +
           openssl) + tests.                                   (~45 min)

Step 4:    Implementar factory.py (selector por env var) +
           tests.                                              (~20 min)

Step 5:    Implementar tools/certs.py (cert_list, cert_get,
           cert_domains_update) usando backend abstracto +
           tests con mock backend.                             (~60 min)

Step 6:    Implementar tools/certs.py (cert_issue,
           cert_deploy_files) — son las que más backend usan +
           tests.                                              (~60 min)

Step 7:    Implementar tools/nginx.py (test/reload) +
           tools/validate.py (cert_validate via openssl, no
           backend) + tests.                                   (~30 min)

Step 8:    Wire 8 tools en server.py (FastMCP) + ajuste
           plugin.toml con credential_refs completos.          (~20 min)

Step 9:    test_no_hardcoded_strings.py — grep contra IPs,
           dominios, paths del operador. Forzar anonimización. (~20 min)

Step 10:   README.md — documentar:
           - Para qué sirve cada tool
           - Cómo elegir backend (wrapper-lxc vs direct-ssh)
           - Qué env vars setear por backend
           - Ejemplos de uso (con valores ficticios)           (~30 min)

Step 11:   Smoke test end-to-end (backend wrapper-lxc del operador):
           - cert_list → cert id=1 visible
           - cert_validate("<dominio del operador>") → SANs ok
           - nginx_test → syntax ok
           NO mutaciones sin OK.                               (~15 min)

Step 12:   Commit + push (rama propia mimir-mcp/feature/nginx-ui-ops-v0.1.0).
           PARO. Operador valida y mergea cuando confirme.

Step 13 (futuro, no en este plan):
           Extracción a CTRQuko/nginx-ui-ops-mcp standalone repo
           cuando esté maduro (v0.2.0+).
```

Total: ~7h efectivas (vs ~4.5h del v2). El 30% extra es el coste de
hacerlo publicable.

---

## Open questions (las restantes tras tu feedback)

1. **Repo de upstream futuro**: ¿`CTRQuko/nginx-ui-ops-mcp` (mismo
   patrón que homelab-mcp y gpon-mcp) o nombre distinto? Recomiendo
   ese mismo patrón para consistencia.

2. **DNS providers a soportar inicialmente**: Cloudflare es seguro
   (operador lo usa). ¿Añadimos también Route53 + DigitalOcean en v0.1.0
   o solo Cloudflare y dejamos los demás para PRs externos? Mi
   recomendación: solo Cloudflare en v0.1.0 (validado), `dns_provider`
   parameter ya prepara la abstracción para añadir otros sin cambios
   de signature.

3. **`cert_issue` rate-limit safety**: Let's Encrypt tiene rate limits
   (50 certs/semana por dominio). Si alguien automatiza llamadas a
   `cert_issue` en bucle, puede quemar la cuota. ¿Implementamos:
   - (a) Idempotencia por defecto: no re-emite si cert actual <30d para
     vencer (override con `force=True`)?
   - (b) Lock file en el host nginx-ui para evitar race con cron de
     renewal de acme.sh?
   - (c) Tracking en una tabla local (sqlite plugin propio) de últimas
     emisiones?
   Recomiendo a+b. (c) es overkill.

4. **Mutations gating**: ¿el plugin habilita las 8 tools por default,
   o las que mutan (`cert_issue`, `cert_domains_update`,
   `cert_deploy_files`, `nginx_reload`) requieren un flag manifest tipo
   `[security].allow_mutations = true`? Recomiendo flag — protección
   contra LLMs over-eager. Default false significa que un operador que
   solo quiere read-only para diagnosticar no se expone a mutaciones
   accidentales.

5. **Versión inicial publicable**: ¿v0.1.0 con backends `wrapper-lxc`
   y `direct-ssh` (lo que cubre tu caso + el genérico) o esperamos
   tener un tercer backend (Docker / Local) antes de publicar? Mi
   recomendación: v0.1.0 con los dos. La interfaz queda probada y
   otros backends se pueden añadir como PRs.

---

## Verification end-to-end

```python
# Caso 1: leer estado del backend del operador
NGINXUI_BACKEND=wrapper-lxc
NGINXUI_PVE_SSH_ALIAS=pve2
NGINXUI_LXC_ID=104

cert_list()                                    # → 1+ cert
nginx_test()                                   # → syntax ok
nginx_cert_validate("<operator domain>")       # → SANs + days_remaining

# Caso 2: mismo plugin, otro backend (validar portabilidad)
NGINXUI_BACKEND=direct-ssh
NGINXUI_HOST=10.20.30.40
NGINXUI_SSH_USER=root
NGINXUI_SUDO_METHOD=none

cert_list()                                    # → debe funcionar también

# Caso 3: backend desconocido falla loud
NGINXUI_BACKEND=docker
# → PluginConfigError "NGINXUI_BACKEND='docker' unknown..."

# Caso 4: anonimización CI
pytest test_no_hardcoded_strings.py            # → 0 IPs/dominios del operador
```

---

## Riesgos / mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Sobre-ingeniería: 2 backends = +30% código | Es el coste mínimo de publicable. Si solo hubiera 1 backend, el ABC es overkill — pero al haber 2, fuerza interface limpia |
| Backend ABC muy restrictiva → futuros backends no encajan | Diseñada con los 6 métodos suficientes para los 8 tools. Si futuro backend requiere algo más, se añade como método opcional con default `NotImplementedError` |
| Operador acaba teniendo que modificar `wrapper-lxc` → desincronización con upstream | Plugin se publica antes de modificarlo. Si necesita cambios, PR a upstream o fork |
| Tests test_no_hardcoded_strings rompen al pegar un dominio en docstring | Whitelist explícita de "ejemplo" strings en el test (e.g. `example.com`, `10.20.30.40` para docs) |
| Otro backend (Docker, Local) requiere nuevos métodos en ABC | v0.1.0 publica con 2 backends; v0.2.0 añade el 3º si aparece. ABC se versiona |

---

## Próximo paso

Cuando respondas las 5 open questions arriba, ejecuto los 12 steps en
~7h efectivas, commit por step, push a una branch
`feature/nginx-ui-ops-v0.1.0` en `mimir-mcp` (NO main upstream del
plugin todavía — primero validamos en monorepo, después extracción).

Pre-step (rotación de secrets) sigue siendo responsabilidad tuya antes
de empezar — necesario para el smoke test del Step 11.
