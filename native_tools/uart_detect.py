"""Detección automática de dispositivos en puertos serie."""
import re
import time

import serial
import serial.tools.list_ports


def detectar_dispositivo_uart(
    puerto: str,
    baudrate: int = 115200,
    timeout_cmd: float = 3.0,
    line_ending: str = "\n",
) -> dict:
    """Conecta a un puerto serie e intenta identificar el dispositivo.

    Prueba comandos de identificación comunes y devuelve lo que responda.
    Si el dispositivo está en silencio o en un bootloader, lo indica.

    Args:
        puerto: Nombre del puerto (ej. "COM4", "/dev/ttyUSB0").
        baudrate: Velocidad en baudios (default 115200).
        timeout_cmd: Timeout por comando en segundos (default 3.0).
        line_ending: Terminador de línea al enviar comandos.
            Por defecto "\\n"; algunos dispositivos (viejos uC, algunos U-Boot)
            requieren "\\r\\n".
    """
    result = {
        "puerto": puerto,
        "conectado": False,
        "dispositivo": "desconocido",
        "sistema": None,
        "kernel": None,
        "hostname": None,
        "respuesta_cruda": None,
        "notas": [],
    }

    # Verificar que el puerto existe
    puertos_disponibles = [p.device for p in serial.tools.list_ports.comports()]
    if puerto not in puertos_disponibles:
        result["notas"].append(f"Puerto {puerto} no encontrado. Disponibles: {puertos_disponibles}")
        return result

    try:
        ser = serial.Serial(
            port=puerto,
            baudrate=baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=1,
            write_timeout=1,
        )
    except serial.SerialException as e:
        result["notas"].append(f"No se pudo abrir {puerto}: {e}")
        return result

    result["conectado"] = True

    try:
        # Limpiar buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.3)

        # Leer cualquier saludo/boot que esté esperando
        boot_greeting = ""
        t0 = time.time()
        while time.time() - t0 < 1.5:
            if ser.in_waiting:
                data = ser.read(ser.in_waiting)
                boot_greeting += data.decode("utf-8", errors="replace")
            time.sleep(0.1)

        if boot_greeting.strip():
            result["respuesta_cruda"] = boot_greeting.strip()
            result["notas"].append("El dispositivo envió datos sin pedir (boot greeting / prompt)")

            # Detectar U-Boot
            if "U-Boot" in boot_greeting or "autoboot" in boot_greeting.lower():
                result["dispositivo"] = "bootloader (U-Boot probable)"
                result["notas"].append("Detectado U-Boot. El dispositivo está en modo bootloader.")
                return result

            if re.search(r"[#$>](\s*)$", boot_greeting.rstrip("\r\n").splitlines()[-1] if boot_greeting.strip() else ""):
                result["notas"].append("Parece haber un prompt de shell activo.")

        # Intentar comandos de identificación
        comandos = [
            ("uname -a", "kernel"),
            ("cat /proc/version", "kernel_alt"),
            ("hostname", "hostname"),
            ("cat /tmp/sysinfo/model", "modelo_openwrt"),
            ("uci get system.@system[0].hostname", "hostname_uci"),
            ("fw_printenv version", "version_uboot"),
            ("version", "version_generica"),
            ("help", "help"),
        ]

        # Post-write settle time: capeado a timeout_cmd para que el parámetro
        # controle efectivamente el tiempo total (evita 5s+ con timeout_cmd=0.1).
        _settle = min(0.4, max(0.05, timeout_cmd))
        for cmd, tipo in comandos:
            ser.write(f"{cmd}{line_ending}".encode())
            ser.flush()
            time.sleep(_settle)

            respuesta = ""
            t0 = time.time()
            while time.time() - t0 < timeout_cmd:
                if ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    respuesta += data.decode("utf-8", errors="replace")
                else:
                    if respuesta.strip():
                        break
                time.sleep(0.1)

            # Limpiar eco del comando
            lines = respuesta.strip().splitlines()
            cleaned_lines = []
            for line in lines:
                line_stripped = line.strip()
                if line_stripped.lower() == cmd.lower():
                    continue
                if line_stripped.startswith(cmd.split()[0]) and len(line_stripped) < len(cmd) + 5:
                    continue
                cleaned_lines.append(line_stripped)

            cleaned = "\n".join(cleaned_lines).strip()

            if cleaned and len(cleaned) > 2:
                if tipo == "kernel" and "Linux" in cleaned:
                    result["kernel"] = cleaned[:300]
                    result["sistema"] = "Linux"
                elif tipo == "kernel_alt" and "Linux" in cleaned:
                    if not result["kernel"]:
                        result["kernel"] = cleaned[:300]
                        result["sistema"] = "Linux"
                elif tipo == "hostname" and len(cleaned) < 100 and " " not in cleaned:
                    result["hostname"] = cleaned
                elif tipo == "modelo_openwrt" and cleaned:
                    result["dispositivo"] = f"OpenWrt / {cleaned[:80]}"
                elif tipo == "version_uboot" and "U-Boot" in cleaned:
                    result["dispositivo"] = "bootloader (U-Boot)"
                    result["notas"].append(f"Versión U-Boot: {cleaned[:200]}")
                elif tipo == "help" and "commands" in cleaned.lower():
                    result["notas"].append("El dispositivo expone una lista de comandos internos.")

        # Inferencia final
        if result["sistema"] == "Linux" and result["kernel"]:
            # Intentar extraer arquitectura y versión
            parts = result["kernel"].split()
            if len(parts) >= 2:
                result["notas"].append(f"Kernel detectado: {parts[1]}")
            if "mips" in result["kernel"].lower():
                result["notas"].append("Arquitectura: MIPS (router/embedded común)")
            elif "arm" in result["kernel"].lower():
                result["notas"].append("Arquitectura: ARM")
            elif "x86" in result["kernel"].lower():
                result["notas"].append("Arquitectura: x86/x86_64")

            if result["dispositivo"] == "desconocido":
                result["dispositivo"] = "Dispositivo Linux embebido (router/GPON stick/OpenWrt)"

        if result["hostname"]:
            result["notas"].append(f"Hostname: {result['hostname']}")

        if not result["sistema"] and not result["respuesta_cruda"]:
            result["notas"].append("El dispositivo no respondió a comandos. Puede estar apagado, en modo bootloader sin prompt, o usar baudrate diferente.")

    except Exception as e:
        result["notas"].append(f"Error durante detección: {e}")
    finally:
        try:
            ser.close()
        except Exception:
            pass

    return result
