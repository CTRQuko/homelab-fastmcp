import asyncio

from fastmcp import Client


async def main():
    async with Client('C:/homelab/laboratorio/homelab-fastmcp/server.py') as client:
        # 1. Conectar a COM4
        print('[1] Conectando a COM4...')
        r = await client.call_tool('uart_uart_conectar', {
            'proyecto': 'detect_com4',
            'puerto': 'COM4',
            'dispositivo': 'unknown',
            'baudrate': 115200
        })
        print(r)
        print()

        # 2. Ver estado
        print('[2] Estado...')
        r2 = await client.call_tool('uart_uart_estado', {})
        print(r2)
        print()

        # 3. Enviar comandos de identificación
        for cmd in ['whoami', 'uname -a', 'cat /proc/version', 'help', 'version']:
            print(f'[3] Comando: "{cmd}"')
            try:
                r3 = await client.call_tool('uart_uart_comando', {'cmd': cmd})
                text = str(r3)
                if text and len(text) > 10:
                    print(f'  Respuesta: {text[:400]}')
                    break
                else:
                    print('  (sin respuesta significativa)')
            except Exception as e:
                print(f'  Error: {e}')
            await asyncio.sleep(0.5)

        # 4. Desconectar
        print()
        print('[4] Desconectando...')
        r4 = await client.call_tool('uart_uart_desconectar', {})
        print(r4)

asyncio.run(main())
