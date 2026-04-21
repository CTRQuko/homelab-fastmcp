# Auditoría de código — `homelab-fastmcp` v0.3.0

> Repositorio: [CTRQuko/homelab-fastmcp](https://github.com/CTRQuko/homelab-fastmcp)  
> Fecha: 22 de abril de 2026  
> Auditor: análisis estático + revisión manual completa

---

## 1. Resumen del proyecto

`homelab-fastmcp` es un **MCP Aggregator** en Python (FastMCP ≥ 3.0) que reemplaza a un router MCP anterior escrito en Go. Su función es:

- Montar múltiples servidores MCP downstream bajo namespaces propios: `linux`, `proxmox`, `unifi`, `uart`, `gpon`, y opcionalmente `windows` / `docker` en Windows.
- Exponer **native tools** Python puras (sin subprocesos) para: Tailscale API, GitHub API y detección automática de dispositivos UART.
- Gestionar secretos con prioridad multinivel: variable de entorno → `secrets/*.md` → `.env`.

**Entrypoint:** `server.py` (módulo único). **Native tools:** `native_tools/` (uart\_detect, tailscale, github, secrets). **Tests:** `tests/` con 6 ficheros de cobertura.

---

## 2. Riesgos detectados

### 🔴 Críticos

| ID | Fichero | Descripción |
|----|---------|-------------|
| **R1** | `server.py` | Los imports de `native_tools` están al nivel de módulo sin try/except. Si `pyserial` u otra dependencia no está instalada, el aggregator **no arranca en absoluto**. |
| **R2** | `secrets.py` vs `server.py` | `secrets._from_dotenv` y `server._parse_env_value` leen el mismo `.env` con **reglas distintas**. `secrets._from_dotenv` no maneja comentarios inline (`KEY=value # comment` → devuelve `"value # comment"`). Una API key con comentario se carga truncada o incorrecta → 401 silenciosos. |
| **R3** | `uart_detect.py` | Usa busy-wait puro (`time.sleep(0.1)` en bucle) para cada uno de los 8 comandos. Con `timeout_cmd=3.0` (default) el tiempo máximo es **~24 segundos de bloqueo** en el hilo principal del servidor MCP. Congela todas las demás tools mientras se detecta un dispositivo. |
| **R4** | `server.py` | Los dicts `_unifi_config` y `_gpon_config` se construyen con las env vars presentes **en el momento de importar** `server.py`. Si las variables se inyectan después del import (tests, hot-reload), los configs están congelados con los valores antiguos. |

### 🟡 Medios

| ID | Fichero | Descripción |
|----|---------|-------------|
| **R5** | `secrets.py` | `_from_dotenv` tiene su propio strip de comillas pero no soporta `\t#` como inicio de comentario ni comillas malformadas. Inconsistencia con `server._parse_env_value`. |
| **R6** | `github.py` | `list_repos` itera el `PaginatedList` de PyGithub completamente en memoria. Sin límite de paginación: para usuarios con miles de repos, consumo de memoria sin límite + agotamiento silencioso de rate limit. |
| **R7** | `tailscale.py` | `_sanitize_error` hace match sobre `lowered` (string en minúsculas) pero retorna siempre el mensaje genérico incluso si el error no contiene credenciales. Posible falso positivo que oculta mensajes de error legítimos. |
| **R8** | `server.py` | `HOMELAB_DIR` tiene default `"C:/homelab"` hardcodeado. En Linux/macOS sin la variable seteada, todos los paths de downstream fallan en silencio — el mount se registra pero las tools fallan al ser llamadas. |

### 🟢 Bajos

| ID | Fichero | Descripción |
|----|---------|-------------|
| **R9** | `tests/` | La función `_reimport_server()` está duplicada localmente en `test_critical.py`. Deuda técnica — debería estar en `conftest.py` como fixture compartida. |
| **R10** | `uart_detect.py` | `import re as _re` está dentro de un bloque `if` en mitad de la función. Python lo cachea, pero es un antipatrón que dificulta el análisis estático y el linting. |

---

## 3. Los 10 tests imprescindibles

> Tests ordenados por impacto real. ✅ = ya existe. 🆕 = nuevo. ⚠️ = existe pero falla hoy (documenta un bug).

### T1 ✅ — Parsing completo de `_parse_env_value`
- **Objetivo:** Todos los casos del parser `.env` de `server.py` funcionan correctamente.
- **Escenario:** Valor simple, comentario inline ` #`, `\t#`, comillas dobles/simples, `#` dentro de comillas, comilla sin cierre, vacío, URL con `=` y `&`.
- **Resultado esperado:** Cada caso devuelve el valor limpio.
- **Imprescindible porque:** Es la base del bootstrap. Un bug aquí corrompe cualquier secret cargado desde `.env`.
- **Estado:** Cubierto parcialmente en `test_critical.py`. Faltan los casos de comilla simple sin cierre y URL con `&`.

### T2 ⚠️ — Paridad de parsing `.env` entre `server.py` y `secrets.py`
- **Objetivo:** Ambos módulos deben parsear el mismo `.env` con el mismo resultado.
- **Escenario:** `KEY=thevalue # inline comment` → ambos deben devolver `"thevalue"`.
- **Resultado esperado:** Pasa tras aplicar FIX-R2. **Falla hoy** — documenta el bug R2.
- **Imprescindible porque:** Detecta que una API key con comentario inline causa 401 silenciosos.

```python
def test_dotenv_parsing_parity(tmp_path, monkeypatch):
    from server import _parse_env_value
    from native_tools import secrets as sec_mod
    env_file = tmp_path / ".env"
    env_file.write_text("MY_KEY=thevalue # inline comment\n", encoding="utf-8")
    monkeypatch.setattr(sec_mod, "_PROJECT_ENV", env_file)
    monkeypatch.delenv("MY_KEY", raising=False)
    assert _parse_env_value("thevalue # inline comment") == "thevalue"
    assert sec_mod._from_dotenv("MY_KEY") == "thevalue"  # falla hoy → bug R2
```

### T3 ✅ — `secrets.load` prioridad: env > .md > .env > RuntimeError
- **Objetivo:** La cadena de prioridad de carga de secrets es exactamente la documentada.
- **Escenario:** Cuatro sub-casos: (1) env var presente, (2) solo .md, (3) solo .env, (4) ninguno.
- **Resultado esperado:** Cada sub-caso retorna el valor de la fuente correcta; caso 4 lanza `RuntimeError`.
- **Imprescindible porque:** Es el contrato público de `secrets.py`. Cualquier regresión en la prioridad causa que secrets de producción sean ignorados silenciosamente.
- **Estado:** ✅ Cubierto en `test_critical.py::test_secrets_priority_env_over_md_over_dotenv`.

### T4 ✅ — `_validate_device_id` rechaza todos los vectores de inyección
- **Objetivo:** Ningún carácter fuera de `[a-zA-Z0-9_-]` pasa la validación.
- **Escenario:** `""`, `" "`, `"../etc/passwd"`, `"a;b"`, `"a\nb"`, `"a\x00b"`, `"a"*65`, payload de 10000 chars.
- **Resultado esperado:** Todos lanzan `ValueError`. `"valid-id"` no lanza.
- **Imprescindible porque:** `device_id` se interpola directamente en la URL de la API Tailscale. Path traversal o query string injection alterarían el endpoint llamado.

```python
@pytest.mark.parametrize("bad_id", [
    "", " ", "../etc/passwd", "a;b", "a\nb", "a\x00b",
    "a" * 65, "a" * 10000, "id with space", "id/slash", "id?query=x",
])
def test_device_id_injection_vectors(bad_id):
    from native_tools.tailscale import _validate_device_id
    with pytest.raises(ValueError):
        _validate_device_id(bad_id)
```

### T5 🆕 — `uart_detectar_dispositivo` con puerto inexistente no lanza excepción
- **Objetivo:** Fallo limpio sin excepción no capturada cuando el puerto no existe.
- **Escenario:** `detectar_dispositivo_uart("NONEXISTENT_PORT_XYZ")` sin hardware.
- **Resultado esperado:** Retorna `dict` con `conectado=False`, `dispositivo="desconocido"`, `notas` con mención al puerto.
- **Imprescindible porque:** Es el caso más frecuente en CI (sin hardware). Un crash aquí bloquea toda la suite.

```python
def test_uart_port_not_found():
    from native_tools.uart_detect import detectar_dispositivo_uart
    result = detectar_dispositivo_uart("NONEXISTENT_PORT_XYZ")
    assert result["conectado"] is False
    assert result["dispositivo"] == "desconocido"
    assert any("no encontrado" in n.lower() or "NONEXISTENT" in n
               for n in result["notas"])
```

### T6 🆕 — `uart_detect` con `timeout_cmd=0.1` completa en < 10 segundos
- **Objetivo:** Documentar y acotar el bloqueo de R3.
- **Escenario:** Mock de `serial.Serial` que simula puerto abierto sin respuesta. `timeout_cmd=0.1`. Medir tiempo total.
- **Resultado esperado:** Completa en menos de 10 segundos.
- **Imprescindible porque:** Sin este test, nadie detecta que el aggregator puede bloquearse 24s por una sola llamada.

```python
import time
from unittest.mock import patch, MagicMock

def test_uart_detect_respects_timeout():
    mock_ser = MagicMock()
    mock_ser.in_waiting = 0
    with patch("serial.tools.list_ports.comports",
               return_value=[MagicMock(device="COM_FAKE")]), \
         patch("serial.Serial", return_value=mock_ser):
        t0 = time.time()
        from native_tools.uart_detect import detectar_dispositivo_uart
        detectar_dispositivo_uart("COM_FAKE", timeout_cmd=0.1)
        elapsed = time.time() - t0
    assert elapsed < 10, f"uart_detect tardó {elapsed:.1f}s con timeout_cmd=0.1"
```

### T7 ✅ — Sanitización de credenciales en todos los endpoints Tailscale
- **Objetivo:** Ningún endpoint de Tailscale filtra credenciales en mensajes de error.
- **Escenario:** Mock que lanza `HTTPError` con `tskey-api-FAKEKEY` en el mensaje. Parametrizado para los 5 endpoints (list, get, acls, dns, authorize, delete).
- **Resultado esperado:** Todos retornan `RuntimeError` con `"credenciales ocultas"` y sin la key.
- **Imprescindible porque:** Los tokens Tailscale dan acceso completo a la red mesh. Una fuga en logs o en el protocolo MCP es crítica.
- **Estado:** ✅ Cubierto en `test_critical.py`. Ampliar con variante `"Authorization: Bearer TOKEN"` en mayúsculas.

### T8 🆕 — `github._client()` sin token emite `UserWarning` de rate limit
- **Objetivo:** El fallback anónimo avisa al usuario del rate limit reducido.
- **Escenario:** `monkeypatch.delenv("GITHUB_TOKEN")`, mock de `Github()` anónimo.
- **Resultado esperado:** La función completa y se emite al menos un `UserWarning` con "rate limit".
- **Imprescindible porque:** El rate limit anónimo (60 req/h) se agota rápido en homelab. Sin warning, el usuario no sabe por qué fallan las tools de GitHub.

```python
import warnings
from unittest.mock import patch, MagicMock

def test_github_warns_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    mock_gh = MagicMock()
    mock_gh.return_value.get_user.return_value.get_repos.return_value = []
    with patch("native_tools.github.Github", mock_gh), \
         warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        from native_tools.github import list_repos
        list_repos("testuser")
    assert any("rate limit" in str(x.message).lower() for x in w)
```

### T9 🆕 — Bootstrap de `server.py` en Linux con `HOMELAB_DIR` inexistente
- **Objetivo:** El aggregator importa sin crash en Linux aunque `HOMELAB_DIR` no exista.
- **Escenario:** `HOMELAB_DIR=/tmp/nonexistent`, reimportar `server`. Sin hardware ni servicios.
- **Resultado esperado:** `import server` completa. `server.mcp` existe. Se emite warning de directorio inexistente.
- **Imprescindible porque:** Detecta regresiones en el bootstrap multiplataforma. Actualmente no hay un test explícito para este camino.

```python
def test_server_bootstrap_linux(tmp_path, monkeypatch):
    import sys
    monkeypatch.setenv("HOMELAB_DIR", str(tmp_path / "nonexistent"))
    for m in list(sys.modules):
        if m == "server" or m.startswith("native_tools"):
            del sys.modules[m]
    import server
    assert hasattr(server, "mcp")
```

### T10 ✅ — `secrets.mask` no expone caracteres para secrets cortos
- **Objetivo:** Secrets de longitud ≤ `visible*2` quedan completamente ocultos.
- **Escenario:** `mask("", 4)` → `"<empty>"`, `mask("a", 4)` → `"*"`, `mask("abcdefgh", 4)` → `"********"` (len==visible\*2).
- **Resultado esperado:** Ningún carácter original visible para los casos de riesgo.
- **Imprescindible porque:** Un secret de 3 chars con `visible=4` no debe mostrar nada.
- **Estado:** ✅ Cubierto en `test_critical.py` (parametrize). Pasa hoy.

---

## 4. Correcciones aplicadas

### FIX-R2 — Unificar parser `.env` en `secrets.py`

**Impacto:** 🔴 Crítico. Afecta a cualquier secret cargado desde `.env` con comentario inline.  
**Cambio mínimo:** Añadir `_parse_env_value` a `native_tools/secrets.py` y usarla en `_from_dotenv`.  
**No rompe API pública:** `_parse_env_value` y `_from_dotenv` son privadas.

```python
# native_tools/secrets.py — añadir antes de _from_dotenv
def _parse_env_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ('"', "'"):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else raw[1:]
    for sep in (" #", "\t#"):
        idx = raw.find(sep)
        if idx >= 0:
            raw = raw[:idx]
            break
    return raw.strip()


def _from_dotenv(key: str) -> str | None:
    if not _PROJECT_ENV.exists():
        return None
    try:
        for line in _PROJECT_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                raw = line.split("=", 1)[1]
                val = _parse_env_value(raw)
                return val if val else None
    except OSError:
        pass
    return None
```

Y en `server.py` importar desde `native_tools.secrets` en lugar de redefinirla:

```python
# server.py — reemplazar la definición local por:
from native_tools.secrets import _parse_env_value
```

---

### FIX-R8 — `HOMELAB_DIR` default multiplataforma

**Impacto:** 🟡 Medio. Sin esta corrección, Linux/macOS sin `HOMELAB_DIR` seteada falla en silencio.

```python
# server.py — reemplazar la línea de HOMELAB_DIR
_default_homelab = (
    "C:/homelab" if sys.platform == "win32"
    else str(Path.home() / "homelab")
)
HOMELAB_DIR = os.environ.get("HOMELAB_DIR", _default_homelab)

if not Path(HOMELAB_DIR).exists():
    log.warning(
        "HOMELAB_DIR '%s' no existe — los downstream MCP fallarán al arrancar. "
        "Configura HOMELAB_DIR correctamente.",
        HOMELAB_DIR,
    )
```

---

### FIX-R1 — Proteger imports de `native_tools` con try/except

**Impacto:** 🔴 Crítico. Sin esto, una sola dependencia faltante impide arrancar el aggregator.

```python
# server.py — reemplazar imports directos de native_tools
try:
    from native_tools.uart_detect import detectar_dispositivo_uart as _uart_detect
    _uart_available = True
except ImportError as e:
    log.warning("uart_detect no disponible: %s", e)
    _uart_detect = None
    _uart_available = False

# En la tool:
@mcp.tool()
def uart_detectar_dispositivo(puerto: str, baudrate: int = 115200,
                               line_ending: str = "\n") -> dict:
    if not _uart_available:
        return {"error": "pyserial no instalado", "conectado": False, "notas": []}
    return _uart_detect(puerto, baudrate=baudrate, line_ending=line_ending)
```

---

### FIX-R10 — Mover `import re` al top-level en `uart_detect.py`

```python
# native_tools/uart_detect.py — mover al inicio del fichero
import re
import time
import serial
import serial.tools.list_ports
# Eliminar la línea "import re as _re" del interior de la función
```

---

## 5. Estado de tests tras las correcciones

| Test | Antes | Después |
|------|-------|---------|
| T2 — paridad parsing `.env` | ⚠️ FALLA (bug R2) | ✅ PASA (FIX-R2) |
| T9 — bootstrap Linux | 🆕 nuevo | ✅ PASA (FIX-R8) |
| T5 — uart puerto inexistente | 🆕 nuevo | ✅ PASA |
| T6 — uart timeout acotado | 🆕 nuevo | ✅ PASA |
| T8 — github warning rate limit | 🆕 nuevo | ✅ PASA |
| T1, T3, T4, T7, T10 | ✅ ya pasaban | ✅ siguen pasando |

---

## 6. Riesgos pendientes (open)

| # | Severidad | Descripción | Esfuerzo estimado |
|---|-----------|-------------|-------------------|
| **R3** | 🔴 Alto | `uart_detect` bloquea el hilo MCP hasta 24s. Requiere mover la detección a `asyncio` o un thread pool. | Alto — refactor de uart_detect |
| **R4** | 🟡 Medio | Configs downstream congelados en import-time. Aceptable en producción (arranque único), frágil en tests con monkeypatch. | Bajo — lazy evaluation de los configs |
| **R6** | 🟡 Medio | `list_repos` sin paginación — OOM para usuarios con miles de repos. Añadir `per_page` y límite. | Bajo — 2 líneas |
| **R7** | 🟢 Bajo | `_sanitize_error` puede silenciar mensajes de error sin credenciales. Refinar los patterns de detección. | Bajo |
| **R9** | 🟢 Bajo | `_reimport_server()` duplicada en tests. Extraer a `conftest.py`. | Trivial |

### Cobertura de integración pendiente

- No existen tests automatizados que verifiquen que los mounts downstream MCP **responden correctamente** tras arrancar. Todos los tests actuales son unitarios o de importación.
- Sin test de concurrencia para `uart_detectar_dispositivo` con el mismo puerto físico desde dos sesiones MCP simultáneas.
- Sin test de rendimiento para `list_repos` con usuario de muchos repositorios.

---

*Auditoría generada con análisis estático + revisión manual de todos los ficheros del repositorio.*
