# Instalacion y Configuracion

## Requisitos

- Python >= 3.11
- `uv` (gestor de entornos y dependencias)
- Git (opcional, para clonar)

## Instalacion

### 1. Clonar o copiar el proyecto

**Windows (recomendado):**
```powershell
cd C:\homelab\laboratorio
git clone <repo> homelab-fastmcp
cd homelab-fastmcp
```

**Linux / macOS:**
```bash
mkdir -p ~/homelab/laboratorio
cd ~/homelab/laboratorio
git clone <repo> homelab-fastmcp
cd homelab-fastmcp
export HOMELAB_DIR=~/homelab
```

### 2. Instalar dependencias

```bash
uv sync
```

O si prefieres pip:
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate  # Windows
pip install -e ".[dev]"
```

### 3. Configurar secrets

**Windows:**
```powershell
New-Item -ItemType Directory -Force -Path "C:\homelab\.config\secrets"
```

**Linux / macOS:**
```bash
mkdir -p ~/homelab/.config/secrets
```

Crear archivos `.md` con secrets:

```markdown
# C:\homelab\.config\secrets\tailscale.md (o ~/homelab/.config/secrets/tailscale.md)
TAILSCALE_API_KEY=tskey-api-XXXXXXXX
TAILSCALE_TAILNET=tu-email@gmail.com
```

```markdown
# C:\homelab\.config\secrets\github-token.md
GITHUB_TOKEN=ghp_XXXXXXXXXXXXXXXX
```

### 4. Configurar .env (opcional)

Copiar `.env` y ajustar:

```bash
# Windows
HOMELAB_DIR=C:/homelab
UNIFI_API_TYPE=local
UNIFI_LOCAL_HOST=192.168.1.12
UNIFI_LOCAL_PORT=11443
UNIFI_LOCAL_VERIFY_SSL=false

# Linux / macOS
HOMELAB_DIR=/home/tuusuario/homelab
UNIFI_API_TYPE=local
UNIFI_LOCAL_HOST=192.168.1.12
UNIFI_LOCAL_PORT=11443
UNIFI_LOCAL_VERIFY_SSL=false
```

### 5. Verificar instalacion

```bash
# Tests
uv run pytest tests/ -v

# Test funcional rapido
uv run python simple_test.py
```

## Configuracion de Downstreams

### Requisitos por plataforma

| Downstream | Windows | Linux | macOS | Notas |
|-----------|---------|-------|-------|-------|
| windows | ✅ | ❌ | ❌ | Requiere PowerShell |
| linux | ✅ | ✅ | ✅ | Requiere hosts SSH configurados |
| proxmox | ✅ | ✅ | ✅ | Requiere `proxmox_nodes.json` |
| docker | ✅ | ❌ | ❌ | Requiere Docker Desktop |
| unifi | ✅ | ✅ | ✅ | Requiere API key |
| uart | ✅ | ✅ | ✅ | Requiere puertos serie |
| gpon | ✅ | ✅ | ✅ | Requiere stick GPON en red |

### Estructura de directorios esperada

```
$HOMELAB_DIR/
├── mcp-servers/
│   ├── homelab-mcp/         # Windows, Linux, Proxmox, Docker
│   ├── mcp-uart-serial/     # UART downstream
│   └── gpon-mcp/            # GPON downstream
├── .config/
│   └── secrets/
│       ├── tailscale.md
│       ├── github-token.md
│       └── ...
└── proyectos/
    └── windows/             # Sandbox de Windows MCP
```

## Solucion de problemas

### "Secret 'X' not found"

Verificar:
1. Variable de entorno: `echo $env:X` (PowerShell) o `echo $X` (bash)
2. Archivo en `$HOMELAB_DIR/.config/secrets/*.md`
3. `.env` en raiz del proyecto

### "401 Unauthorized" en UniFi

Verificar en `.env`:
```
UNIFI_API_TYPE=local
UNIFI_LOCAL_HOST=<ip-del-controller>
UNIFI_LOCAL_VERIFY_SSL=false
```

### "PyGithub no esta instalado"

```bash
uv add PyGithub
```

### Windows no aparece en Linux

Es el comportamiento esperado. `windows_*` y `docker_*` solo se montan en Windows (`sys.platform == "win32"`).
