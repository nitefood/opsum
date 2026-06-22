# Project Binaries

This folder contains prebuilt firmware binaries for self-flashing OPSUM boards.

Current files:

- `firmware-s3-FULL.bin`
- `firmware-s3-OTA.bin`

## What Each Binary Is For

- `firmware-s3-FULL.bin`:

  **Use for first time flashing of a new/blank board. This firmware installs DFU mode and the main firmware on the board**:
  1. Install [esptool](https://docs.espressif.com/projects/esptool/en/latest/esp32/installation.html#how-to-install)
  2. Put the board in bootloader mode: keep the "boot" button pressed while connecting the USB-C cable to your PC
  3. Erase the current flash contents: `esptool erase-flash`
  3. Flash the firmware to the board: `esptool write-flash 0x0 firmware-s3-FULL.bin` 

- `firmware-s3-OTA.bin`:

  **Use to reflash, update or change the main firmware on an OPSUM board**:
  - This is the main firmware image only. Use the included [flash tool](flashtool.exe) to set the board to DFU mode and program it.

- `flashtool.exe`:

  This is the helper tool that reboots the board into DFU mode, and allows flashing the main firmware image.

- `opsum_gui.exe`:

  Minimal host-side GUI tool that communicates with the board and displays the Volt/Amp/Watt readings on screen.


## Compiling the firmware from source

The binaries are produced from MicroPython ESP32-S3 port with the project manifest.

Example (bash):

```bash
MICROPYTHON_PORT_DIR="/path/to/micropython/ports/esp32"
REPO_ROOT="/path/to/opsum"

cd "$MICROPYTHON_PORT_DIR"
make BOARD=ESP32_GENERIC_S3 FROZEN_MANIFEST="$REPO_ROOT/src/Firmware/Core/manifest.py"

cp build-ESP32_GENERIC_S3/micropython.bin "$REPO_ROOT/dist/firmware-s3-OTA.bin"
cp build-ESP32_GENERIC_S3/firmware.bin "$REPO_ROOT/dist/firmware-s3-FULL.bin"
```
