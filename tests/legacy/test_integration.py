"""Test de integración: verifica que el aggregator monta todo."""
import asyncio
import os

import pytest
from fastmcp import Client

SERVER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py")


@pytest.mark.asyncio
async def test_server_info():
    """Verifica nombre del servidor."""
    async with Client(SERVER_PATH) as client:
        result = await client.initialize()
        assert result.serverInfo.name == "Homelab Aggregator"


@pytest.mark.asyncio
async def test_windows_tools():
    """Debe exponer windows_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "windows_read_file" in names
        assert "windows_run_powershell" in names


@pytest.mark.asyncio
async def test_linux_tools():
    """Debe exponer linux_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "linux_read_file" in names
        assert "linux_run_command" in names


@pytest.mark.asyncio
async def test_proxmox_tools():
    """Debe exponer proxmox_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "proxmox_list_nodes" in names
        assert "proxmox_get_vm_status" in names


@pytest.mark.asyncio
async def test_docker_tools():
    """Debe exponer docker_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "docker_list_containers" in names
        assert "docker_inspect_container" in names


@pytest.mark.asyncio
async def test_unifi_tools():
    """Debe exponer unifi_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert any(n.startswith("unifi_") for n in names)


@pytest.mark.asyncio
async def test_uart_tools():
    """Debe exponer uart_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "uart_uart_puertos" in names
        assert "uart_uart_conectar" in names


@pytest.mark.asyncio
async def test_gpon_tools():
    """Debe exponer gpon_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert any(n.startswith("gpon_") for n in names)


@pytest.mark.asyncio
async def test_tailscale_tools():
    """Debe exponer tailscale_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "tailscale_list_devices" in names
        assert "tailscale_get_device" in names
        assert "tailscale_get_acls" in names
        assert "tailscale_get_dns" in names
        assert "tailscale_authorize_device" in names
        assert "tailscale_delete_device" in names


@pytest.mark.asyncio
async def test_github_tools():
    """Debe exponer github_*."""
    async with Client(SERVER_PATH) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "github_list_repos" in names
        assert "github_get_repo_info" in names
        assert "github_get_issue" in names
        assert "github_create_issue" in names
        assert "github_list_prs" in names


if __name__ == "__main__":
    tests = [
        test_server_info,
        test_windows_tools,
        test_linux_tools,
        test_proxmox_tools,
        test_docker_tools,
        test_unifi_tools,
        test_uart_tools,
        test_gpon_tools,
        test_tailscale_tools,
        test_github_tools,
    ]
    for t in tests:
        asyncio.run(t())
    print("All tests passed!")
