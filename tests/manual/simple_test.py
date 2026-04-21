"""Suite simple sin adornos — valida que todos los servicios responden.

Uso: uv run python simple_test.py
"""
import asyncio

from fastmcp import Client

SERVER = "C:/homelab/laboratorio/homelab-fastmcp/server.py"

TESTS = [
    ("windows_list_dir", {}),
    ("windows_run_powershell", {"cmd": "Get-ChildItem"}),
    ("linux_run_command", {"host": "pve", "cmd": "hostname"}),
    ("proxmox_list_nodes", {"node": "pve"}),
    ("proxmox_get_node_status", {"node": "logrono"}),
    ("docker_list_containers", {}),
    ("unifi_list_all_sites", {}),
    ("tailscale_list_devices", {}),
    ("github_list_repos", {"user": "octocat"}),
]

async def main():
    print("homelab-fastmcp :: validacion simple")
    print("-" * 40)

    ok = 0
    async with Client(SERVER) as client:
        for name, args in TESTS:
            try:
                r = await client.call_tool(name, args)
                txt = str(r)
                fail = '"error"' in txt.lower() or 'toolerror' in txt.lower()
                status = "FAIL" if fail else "OK"
                if not fail:
                    ok += 1
            except Exception as e:
                status = f"FAIL ({type(e).__name__})"
            print(f"  {name:30s} {status}")

    print("-" * 40)
    print(f"resultado: {ok}/{len(TESTS)} OK")
    return ok == len(TESTS)

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
