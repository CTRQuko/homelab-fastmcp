"""Prueba UART y GPON con hardware conectado. SOLO LECTURA."""
import asyncio

from fastmcp import Client

SERVER = "C:/homelab/laboratorio/homelab-fastmcp/server.py"

async def main():
    print("UART/GPON :: prueba con hardware :: SOLO LECTURA")
    print("=" * 50)

    async with Client(SERVER) as client:
        # --- UART ---
        print("\n[UART] 1. Listar puertos serie...")
        try:
            r = await client.call_tool("uart_uart_puertos", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        print("\n[UART] 2. Info del servidor UART...")
        try:
            r = await client.call_tool("uart_uart_info", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        print("\n[UART] 3. Listar sesiones...")
        try:
            r = await client.call_tool("uart_uart_sesiones", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        # --- GPON ---
        print("\n[GPON] 4. Listar sticks...")
        try:
            r = await client.call_tool("gpon_list_sticks", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        print("\n[GPON] 5. Detectar modulo...")
        try:
            r = await client.call_tool("gpon_detect_module", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        print("\n[GPON] 6. Estado del stick...")
        try:
            r = await client.call_tool("gpon_get_status", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        print("\n[GPON] 7. Listar puertos UART (desde GPON)...")
        try:
            r = await client.call_tool("gpon_uart_list_ports", {})
            print(f"  Resultado: {str(r)[:500]}")
        except Exception as e:
            print(f"  Error: {type(e).__name__}: {e}")

        print("\n" + "=" * 50)
        print("Prueba completada")

if __name__ == "__main__":
    asyncio.run(main())
