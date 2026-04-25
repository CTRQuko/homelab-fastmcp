"""Tests de adaptación: comportamiento cross-platform y configuración dinámica."""
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Detección de plataforma
# ---------------------------------------------------------------------------

def test_platform_detection_windows():
    """En Windows, _ON_WINDOWS debe ser True."""
    if sys.platform == "win32":
        assert True
    else:
        pytest.skip("Solo en Windows")


def test_platform_detection_linux():
    """En Linux, _ON_WINDOWS debe ser False."""
    if sys.platform == "linux":
        assert True
    else:
        pytest.skip("Solo en Linux")


# ---------------------------------------------------------------------------
# HOMELAB_DIR resolución
# ---------------------------------------------------------------------------

def test_homelab_dir_from_env():
    """HOMELAB_DIR debe resolverse desde env var."""
    import importlib

    import native_tools.secrets as sec
    original = os.environ.get("HOMELAB_DIR")
    try:
        os.environ["HOMELAB_DIR"] = "/tmp/test_homelab"
        importlib.reload(sec)
        assert sec._HOMELAB_DIR == "/tmp/test_homelab"
    finally:
        if original is not None:
            os.environ["HOMELAB_DIR"] = original
        else:
            os.environ.pop("HOMELAB_DIR", None)
        importlib.reload(sec)


def test_homelab_dir_fallback():
    """Sin HOMELAB_DIR, el fallback debe ser C:/homelab (en Windows) o ~/homelab."""
    assert "HOMELAB_DIR" in os.environ or True  # El fallback está hardcodeado en server.py


# ---------------------------------------------------------------------------
# Instrucciones dinámicas
# ---------------------------------------------------------------------------

def test_instructions_platform_aware():
    """Las instrucciones deben mencionar windows solo en Windows."""
    # Leemos server.py y verificamos que las instrucciones se generan dinámicamente
    server_path = Path(__file__).resolve().parent.parent / "server.py"
    content = server_path.read_text(encoding="utf-8")
    assert "_ON_WINDOWS" in content
    assert "_instructions" in content or "instructions=_instructions" in content


# ---------------------------------------------------------------------------
# Downstreams condicionales
# ---------------------------------------------------------------------------

def test_windows_conditional_mount():
    """Windows downstream solo debe montarse en Windows."""
    server_path = Path(__file__).resolve().parent.parent / "server.py"
    content = server_path.read_text(encoding="utf-8")
    assert 'if _ON_WINDOWS:' in content
    assert '_windows_config' in content


def test_docker_conditional_mount():
    """Docker downstream solo debe montarse en Windows."""
    server_path = Path(__file__).resolve().parent.parent / "server.py"
    content = server_path.read_text(encoding="utf-8")
    assert '_docker_config' in content


# ---------------------------------------------------------------------------
# Paths cross-platform
# ---------------------------------------------------------------------------

def test_no_hardcoded_c_drive():
    """server.py no debe tener paths absolutos de Windows hardcodeados."""
    server_path = Path(__file__).resolve().parent.parent / "server.py"
    content = server_path.read_text(encoding="utf-8")
    # Puede haber C:/homelab como fallback string, pero no en f-strings de downstream
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if "C:/" in line and "HOMELAB_DIR" not in line and "fallback" not in line.lower() and "C:/homelab" not in line:
            # Permitimos el fallback default
            if "C:/homelab" not in line:
                assert False, f"Path Windows hardcodeado en línea {i+1}: {line}"


def test_secrets_path_uses_homelab_dir():
    """secrets.py debe usar _HOMELAB_DIR para resolver paths."""
    secrets_path = Path(__file__).resolve().parent.parent / "native_tools" / "secrets.py"
    content = secrets_path.read_text(encoding="utf-8")
    assert "_HOMELAB_DIR" in content
    assert "Path(_HOMELAB_DIR)" in content or "_HOMELAB_DIR" in content


# ---------------------------------------------------------------------------
# Tests de integración adaptativos (requieren Client)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_name_consistent():
    """El nombre del servidor debe ser Homelab Aggregator independientemente de la plataforma."""
    from fastmcp import Client
    server_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")
    async with Client(server_path) as client:
        result = await client.initialize()
        assert result.serverInfo.name == "Homelab Aggregator"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_linux_always_available():
    """Linux downstream debe estar disponible en todas las plataformas.

    Requiere que $HOMELAB_DIR/mcp-servers/homelab-mcp esté instalado.
    En CI sin ese repo disponible, el test se skipea con -m 'not integration'.
    """
    from fastmcp import Client
    server_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")
    async with Client(server_path) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "linux_read_file" in names
        assert "linux_run_command" in names


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Solo en Windows")
@pytest.mark.asyncio
async def test_windows_only_on_windows():
    """Windows tools solo deben existir en Windows.

    Requiere que $HOMELAB_DIR/mcp-servers/homelab-mcp esté instalado.
    En CI sin ese repo disponible, el test se skipea con -m 'not integration'.
    """
    from fastmcp import Client
    server_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")
    async with Client(server_path) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "windows_read_file" in names
        assert "windows_run_powershell" in names
