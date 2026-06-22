# OPSUM DFU

This directory contains a minimal (~256Kb) native ESP-IDF DFU updater written in C and built with PlatformIO.

> If you only need to program and use boards (not modify DFU firmware), follow the instructions under [/dist](../../../dist/).

## Board Target

- PlatformIO environment: `esp32s3_generic_n4r2`
- Actual board definition in use: `esp32-s3-devkitc-1` (generic ESP32-S3)
- Expected memory profile: 4 MB flash + 2 MB PSRAM (for boards like ESP32-S3-Zero N4R2 variants)

## DFU Firmware Overview

- Dedicated DFU app running from partition `dfu` (`ota_0`)
- Updates only partition `app` (`ota_1`)
- Erases partition `storage` (1 MB LittleFS area) only after successful image verification
- UART DFU protocol only on `TX=GPIO3`, `RX=GPIO4`
- Built-in NeoPixel status indication on `GPIO21`: solid yellow in IDLE/READY, fast yellow blink during active transfer
- Integrity verification uses CRC32.

## Protocol Details

- DFU UART protocol: [/protocol-specs/dfu-uart.md](../../../protocol-specs/dfu-uart.md)
- Core runtime UART protocol (for mode detection and runtime control): [/protocol-specs/core-uart.md](../../../protocol-specs/core-uart.md)

## Build

From this folder:

```powershell
pio run -e esp32s3_generic_n4r2
```

## Flash DFU app

```powershell
pio run -e esp32s3_generic_n4r2 -t upload
```

## Flash Layout and Partition Setup

This section describes how to prepare the board flash layout so the DFU updater can manage a dedicated main application partition plus mandatory 1 MB storage partition.

### 1. Partition map

Used file: `partitions_dfu.csv`

| Name | Type/Subtype | Offset | Size | Purpose |
|---|---|---:|---:|---|
| nvs | data/nvs | 0x9000 | 0x6000 (24 KB) | key-value config |
| otadata | data/ota | 0xF000 | 0x2000 (8 KB) | OTA boot metadata |
| phy_init | data/phy | 0x11000 | 0x1000 (4 KB) | PHY init |
| dfu | app/ota_0 | 0x20000 | 0x40000 (256 KB) | DFU updater app |
| app | app/ota_1 | 0x60000 | 0x2A0000 (2.625 MB) | main firmware slot |
| storage | data/littlefs (0x83) | 0x300000 | 0x100000 (1 MB) | 1MB LittleFS storage |

### 2. Prerequisites

- Python + PlatformIO CLI installed
- USB serial access to the ESP32-S3 board
- Board in ROM download mode for first flash (manual boot/reset procedure as required by board)

From repository root:

```powershell
Set-Location "src/Firmware/DFU"
```

### 3. Build the DFU project

```powershell
pio run -e esp32s3_generic_n4r2
```

This produces:

- bootloader binary
- partition table binary
- DFU app binary

### 4. Initial full flash (recommended)

Option A (simplest):

```powershell
pio run -e esp32s3_generic_n4r2 -t upload
```

This flashes bootloader + partition table + DFU app with the configured partition CSV.

### 5. Manual explicit flashing (if you want full control)

1. Identify serial port (example `COM7`).
2. Use files generated under `.pio/build/esp32s3_generic_n4r2/`.

Example (adjust `COMx`):

```powershell
esptool --chip esp32s3 --port COM7 --baud 921600 erase_flash
esptool --chip esp32s3 --port COM7 --baud 921600 write_flash -z `
	0x0000 .pio/build/esp32s3_generic_n4r2/bootloader.bin `
	0x8000 .pio/build/esp32s3_generic_n4r2/partitions.bin `
	0x20000 .pio/build/esp32s3_generic_n4r2/firmware.bin
```

Notes:

- `firmware.bin` here is the DFU updater image and is placed at `dfu` (`ota_0`) offset `0x20000`.
- `otadata` is created and managed by ESP-IDF OTA APIs during runtime.

### 6. Verify flash layout on device

After flashing and reset, connect monitor:

```powershell
pio device monitor -b 115200
```

>Note: `pio device monitor -b 115200` only opens the serial terminal; it does not force DFU mode by itself. Press reset (or power-cycle) to observe DFU boot logs and LED status.

The DFU app should boot and expose the DFU UART protocol on GPIO3/GPIO4.
In DFU firmware, the NeoPixel on GPIO21 is:

- Solid yellow in `IDLE/READY`
- Fast yellow blink during active transfer (`RECEIVING/VERIFYING`)

The DFU app also prints a startup line and a periodic heartbeat (`DFU alive: ...`) every 5 seconds at warning level.

### 7. DFU path validation sequence

Steps to validate the DFU firmware and protocol path end-to-end:

1. Flash DFU image using this layout.
2. Use host DFU protocol to program main app image into `app` (`ota_1`).
3. On successful `END`, DFU erases `storage` and sets boot target to `app`.
4. Reboot to run main firmware.

### 8. Maintenance commands

Rebuild DFU:

```powershell
pio run -e esp32s3_generic_n4r2
```

Reflash DFU only:

```powershell
pio run -e esp32s3_generic_n4r2 -t upload
```

If you need a clean re-provision:

```powershell
esptool --chip esp32s3 --port COM7 erase_flash
```
