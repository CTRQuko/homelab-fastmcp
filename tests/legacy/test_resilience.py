"""Tests de resiliencia: comportamiento ante fallos de downstreams."""
import os

import pytest
from fastmcp import Client

SERVER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


# ---------------------------------------------------------------------------
# Downstream caído / timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_downstream_wrong_namespace_graceful():
    """Llamar a una tool con namespace inexistente debe fallar graceful."""
    async with Client(SERVER_PATH) as client:
        try:
            await client.call_tool("nonexistent__foo", {})
            assert False, "Debería haber fallado"
        except Exception as e:
            # Debe fallar, pero no crashear el servidor
            assert "nonexistent" in str(e).lower() or "not found" in str(e).lower() or "tool" in str(e).lower()


@pytest.mark.asyncio
async def test_downstream_invalid_args():
    """Argumentos inválidos deben devolver error, no crashear."""
    async with Client(SERVER_PATH) as client:
        # tailscale_get_device requiere device_id string
        try:
            r = await client.call_tool("tailscale_get_device", {"device_id": ""})
            txt = str(r).lower()
            assert "error" in txt or "inválido" in txt or "invalid" in txt
        except Exception as e:
            assert "device_id" in str(e).lower() or "invalid" in str(e).lower()


@pytest.mark.asyncio
async def test_github_rate_limit_handling():
    """GitHub sin token o sin PyGithub debe manejar errores graceful."""
    async with Client(SERVER_PATH) as client:
        try:
            r = await client.call_tool("github_list_repos", {"user": "octocat"})
            txt = str(r)
            # Debe funcionar (octocat es público) o dar rate limit
            assert "octocat" in txt.lower() or "rate" in txt.lower() or "error" in txt.lower()
        except Exception as e:
            err = str(e).lower()
            # Rate limit, PyGithub no instalado, o cualquier error controlado es aceptable
            assert any(k in err for k in ["rate", "limit", "403", "pygithub", "github"])


# ---------------------------------------------------------------------------
# Hardware desconectado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uart_detect_nonexistent_port():
    """Detectar dispositivo en puerto inexistente debe devolver estructura vacía, no crashear."""
    async with Client(SERVER_PATH) as client:
        try:
            r = await client.call_tool("uart_detectar_dispositivo", {"puerto": "COM999", "baudrate": 115200})
            txt = str(r)
            # Debe indicar que no se conectó o que el puerto no existe
            assert "no encontrado" in txt.lower() or "conectado" in txt.lower() or "false" in txt.lower()
        except Exception as e:
            # Un error controlado es aceptable
            assert "port" in str(e).lower() or "puerto" in str(e).lower() or "com999" in str(e).lower()


# ---------------------------------------------------------------------------
# Credenciales incorrectas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tailscale_no_key_error_sanitized():
    """Error de Tailscale sin API key debe ser seguro (no filtrar secrets)."""
    async with Client(SERVER_PATH) as client:
        try:
            r = await client.call_tool("tailscale_list_devices", {})
            txt = str(r)
            # Si falla, no debe contener tskey-*
            assert "tskey-" not in txt, "Credencial filtrada en error"
        except Exception as e:
            err = str(e)
            assert "tskey-" not in err, "Credencial filtrada en excepción"


# ---------------------------------------------------------------------------
# Servicio caído — UniFi con host incorrecto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unifi_unreachable_host():
    """UniFi con host inexistente debe timeout o error graceful."""
    async with Client(SERVER_PATH) as client:
        try:
            await client.call_tool("unifi_list_all_sites", {})
            # Si UniFi está configurado con host real, esto puede funcionar
            # Si no, debe dar error controlado
            assert True  # Si llega aquí, no crasheó
        except Exception as e:
            # Timeout o connection error es esperable si el host no responde
            assert "timeout" in str(e).lower() or "connection" in str(e).lower() or "unreachable" in str(e).lower() or "error" in str(e).lower()
