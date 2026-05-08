# Mimir MCP — Módulo GPON

Gestión declarativa de ONTs GPON vía SSH/UART. Plugin activo v2.1.0.

---

## 1. Inventario de Sticks

| Herramienta | Descripción |
|------------|-------------|
| `register_stick` | Añadir stick al pool (IP, usuario, password, label) |
| `list_sticks` | Listar todos los sticks registrados |
| `unregister_stick` | Eliminar stick del pool |
| `check_ssh` | Probar conectividad SSH |

---

## 2. Detección y Diagnóstico

| Herramienta | Descripción |
|------------|-------------|
| `detect_module` | Auto-detectar chipset y modelo (Lantiq, Realtek, ZTE, MediaTek) |
| `get_status` | Estado completo: ONU, serial, firmware, particiones |
| `get_logs` | Ver dmesg del stick |
| `search_modules` | Buscar en base hack-gpon.org (por modelo, chipset, vendor) |
| `list_known_modules` | Listar todos los modelos documentados |
| `get_module_docs` | Ver docs completas de un modelo |

### Modelo soportados (ejemplos)

- Huawei MA5671A
- Nokia G-010S-P
- FS.com GPON-ONU-34-20BI
- ZTE F660 / F460
- Mediatek (variales)

---

## 3. Autenticación

| Herramienta | Descripción |
|------------|-------------|
| `get_ploam` | Leer password PLOAM actual |
| `set_ploam` | Establecer password PLOAM (20 hex chars) |
| `get_loid` | Leer LOID y password |
| `set_loid` | Establecer LOID + password |
| `clear_auth` | Limpiar autenticación (PLOAM + LOID) |
| `configure_operator` | Configurar para ISP específico |

### Operadores soportados

| País | Operadores |
|------|------------|
| ES | movistar_es, orange_es, vodafone_es, digi_es, masmovil_es |
| IT | tim_it, fastweb_it |
| UK | bt_uk |
| FR | orange_fr |

---

## 4. Configuración

| Herramienta | Descripción |
|------------|-------------|
| `get_gpon_env` | Leer config normalizada (adapter-aware) |
| `get_full_env` | Leer config raw (UCI completa) |
| `get_serial` | Leer serial GPON |
| `set_serial` | Establecer serial GPON (12 chars) |
| `reboot_stick` | Reiniciar stick |

### Lantiq-only

| Herramienta | Descripción |
|------------|-------------|
| `lantiq_read_sfp_a2` | Leer EEPROM SFP A2 |
| `lantiq_patch_sfp_a2` | Patchear EEPROM (auth, gpon_sn, mac, loid/ploam) |
| `lantiq_minishell` | Ejecutar comando minishell |
| `lantiq_set_lan_speed` | Fijar velocidad LAN (3=1G auto, 4=1G fixed, 5=2.5G) |
| `lantiq_unlock_bootloader` | Desbloquear U-Boot (bootdelay=5) |
| `lantiq_get_active_image` | Ver imagen activa (0 o 1) |
| `lantiq_set_active_image` | Cambiar imagen activa |

---

## 5. Backup y Restauración

| Herramienta | Descripción |
|------------|-------------|
| `backup_config` | Backup completo deconfig |
| `backup_partition` | Copiar partición MTD a /tmp |
| `scp_get_file` | Descargar archivo por SCP |
| `scp_put_file` | Subir archivo por SCP |

---

## 6. UART / Serie

| Herramienta | Descripción |
|------------|-------------|
| `uart_list_ports` | Listar puertos serie disponibles |
| `uart_register` | Registrar sesión serie |
| `uart_auto_detect` | Auto-detectar USB-UART |
| `uart_get_console_type` | Detectar si está en U-Boot o shell |
| `uart_interrupt_boot` | Interrumpir countdown U-Boot |
| `uart_shell_cmd` | Ejecutar comando en shell |
| `uart_read_output` | Leer salida serie (timeout) |

### UART Lantiq (flashing)

| Herramienta | Descripción |
|------------|-------------|
| `uart_get_pinout` | Ver pinout UART por modelo |
| `uart_uboot_cmd` | Ejecutar comando U-Boot |
| `uart_uboot_printenv` | Ver variables U-Boot |
| `uart_lantiq_unlock_bootloader` | Desbloquear U-Boot vía UART |
| `uart_lantiq_start_ymodem_flash` | Iniciar upload YMODEM |
| `uart_lantiq_flash_from_ram` | Escribir flash desde RAM |

---

## 7. Telnet

| Herramienta | Descripción |
|------------|-------------|
| `telnet_connect` | Conectar a sesión Telnet |
| `telnet_send_command` | Ejecutar comando vía Telnet |
| `telnet_disconnect` | Cerrar sesión |

---

## 8. Utilidades

| Herramienta | Descripción |
|------------|-------------|
| `validate_firmware` | Validar firmware (size, MD5, magic bytes) |
| `compare_with_reference` | Comparar config vs reference hack-gpon.org |
| `compare_sticks` | Comparar dos sticks |
| `sync_knowledge_base` | Sincronizar repo hack-gpon.org local |
| `get_audit_logs` | Ver logs de auditoría |

---

## 9. Flujo típico

```
1. register_stick(IP, usuario, password)
2. detect_module() → detecta chipset/modelo
3. configure_operator(operador, serial, ploam_hex | loid)
4. get_status() → verificar registro ONU
```

---

## Requisitos

- Stick reachable vía SSH (puerto 22) o UART (USB-UART)
- Credenciales válidas (admin/te SUPPORT)
- Conocimiento del operador (PLOAM / LOID)

---

*Documento generado para presentación — Mimir MCP v2.1.0*