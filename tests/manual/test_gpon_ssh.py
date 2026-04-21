import asyncio

from fastmcp import Client

SERVER = "C:/homelab/laboratorio/homelab-fastmcp/server.py"

async def main():
    print("GPON :: Prueba SSH a 192.168.100.10")
    print("=" * 50)

    async with Client(SERVER) as client:
        # 1. Registrar stick
        print("\n[1] gpon_register_stick...")
        try:
            r = await client.call_tool("gpon_register_stick", {
                "host": "192.168.100.10",
                "username": "root",
                "password": "1234",
                "port": 22,
                "label": "stick_test"
            })
            print(f"  Resultado: {str(r)[:600]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        # 2. Estado del stick
        print("\n[2] gpon_get_status...")
        try:
            r = await client.call_tool("gpon_get_status", {
                "stick_key": "192.168.100.10:22"
            })
            print(f"  Resultado: {str(r)[:600]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        # 3. Comando simple
        print("\n[3] gpon_execute_command (uname -a)...")
        try:
            r = await client.call_tool("gpon_execute_command", {
                "stick_key": "192.168.100.10:22",
                "command": "uname -a"
            })
            print(f"  Resultado: {str(r)[:600]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        # 4. Detectar módulo
        print("\n[4] gpon_detect_module...")
        try:
            r = await client.call_tool("gpon_detect_module", {
                "stick_key": "192.168.100.10:22"
            })
            print(f"  Resultado: {str(r)[:600]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        # 5. Listar sticks
        print("\n[5] gpon_list_sticks...")
        try:
            r = await client.call_tool("gpon_list_sticks", {})
            print(f"  Resultado: {str(r)[:600]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

    print("\n" + "=" * 50)
    print("Prueba completada")

if __name__ == "__main__":
    asyncio.run(main())
