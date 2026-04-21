# Registro de Bugs - homelab-fastmcp

## Formato de códigos

- **R#**: Bug encontrado en revisión (Review)
- **T#**: Bug encontrado por tests automatizados
- **F#**: Bug encontrado por el usuario (Feedback)

## Bugs resueltos

| Código | Descripción | Estado | Fichero | Fix |
|--------|-------------|--------|---------|-----|
| R2 | Parser .env en secrets._from_dotenv es primitivo (no maneja comillas anidadas ni comentarios) | ✅ Fixed v0.3.2 | secrets.py | Implementa _parse_env_value |
| R5 | Mismo bug que R2 (duplicado) | ✅ Fixed v0.3.2 | secrets.py | Unificado con server.py |
| R8 | HOMELAB_DIR hardcoded C:/homelab (solo Windows) | ✅ Fixed v0.3.2 | server.py | Fallback /home para Linux/macOS |
| R10 | import re inline dentro de función en uart_detect.py | ✅ Fixed v0.3.2 | uart_detect.py | Mover import a nivel módulo |
| R14 | pyserial timeout=0 rechazado por Windows | ✅ Fixed v0.3.1 | server.py | timeout=1 mínimo |
| R17 | Logging a stdout rompe protocolo MCP | ✅ Fixed v0.3.1 | server.py | Logging a stderr |
| R19 | main() no maneja KeyboardInterrupt | ✅ Fixed v0.3.1 | server.py | try/except/finally |
| R21 | UART detect no parsea boot greeting | ✅ Fixed v0.3.1 | uart_detect.py | Detección U-Boot/shell |
| R23 | GitHub API sin auth emite warning | ✅ Fixed v0.3.1 | github.py | Warning en vez de excepción |
| T2 | Falta test de paridad parser .env | ✅ Fixed v0.3.2 | tests/test_critical.py | test_dotenv_parsing_* |
| R16 | Falta test para .env con comillas | ✅ Fixed v0.3.1 | tests/test_coverage_gaps.py | test_dotenv_parsing_* |
| R7 | Reviewer：错误 - _sanitize_error sí retorna original | ✅ Verified | tailscale.py | Sin cambio necesario |

## Bugs pendientes

| Código | Descripción | Estado | Fichero |
|--------|-------------|--------|---------|
| - | Ninguno | - | - |

## Historial de versiones

- **v0.3.2**: Parser .env unificado, HOMELAB_DIR multi-plataforma, import re top-level
- **v0.3.1**: Logging stderr, timeout pyserial, manejo KeyboardInterrupt,tests coverage gaps
- **v0.3.0**: Primer release con parser .env robusto
- **v0.2.x**: Versiones tempranas con bugs R7,R9,R14,R17,R19,R21,R23
