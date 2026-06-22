# OPSUM DFU Flash Tool

This folder contains the source code for the desktop GUI flasher for the dedicated OPSUM _Device Firmware Update_ (**DFU**) partition.

> The DFU communication protocol is documented [here](../../../protocol-specs/dfu-uart.md)

## Supported Features

- UART connection to the board.
- DFU-mode detection via binary `HELLO` / `HELLO_RSP` handshake.
- Automatic baud handling:
  - normal/runtime default: `115200`
  - DFU preferred probe speed: `921600`
  - automatic reconnect/probing during mode detection (no baud dropdown)
- Clear UI mode indication (`Disconnected`, `DFU Mode`, `Normal Mode`, `Unknown`).
- APP image flashing via DFU protocol commands:
  - `HELLO`
  - `BEGIN`
  - `DATA` (sequential chunks)
  - `END`
  - `REBOOT`
- Transfer chunk size automatically uses `HELLO_RSP.max_chunk`, capped at `4096` bytes.
- Transfer progress bar and session log.
- User abort (`ABORT`) during transfer.

## Runtime Mode Detection

Normal mode is detected with cleartext UART command:

- Host sends: `STATUS\r\n`
- Firmware responds: `\r\nOK_RUNNING,{FIRMWARE_STRING}\r\n`

Where:

- `FIRMWARE_STRING = "{FIRMWARE_NAME},{VERSION},{TARGET_BOARD_REV},{BOARD}"`

The tool parses and displays:

- firmware name
- version
- target board revision
- board identifier

## Protocol Assumptions for the DFU Firmware

The tool matches the DFU implementation in `Firmware/DFU/src/main.c`:

- Framing bytes: `0x44 0x46` (`DF`)
- Protocol version: `0x01`
- Sequence numbers must match expected value in DFU target.
- CRC32 behavior matches DFU firmware implementation (`esp_rom_crc32_le` wrapper style).
- Target partition ID for flashing APP: `0x01`
- Typical max chunk from DFU target: `4096` bytes

## Run

Install Python dependencies (from the GUI requirements file):

```bash
pip install -r src/GUI/opsum-gui/requirements.txt
```

Then run the script:

```bash
python src/GUI/flash-tool/flashtool.pyw
```

## Basic Workflow

1. Connect isolated UART to board (`TX=GPIO3`, `RX=GPIO4` on MCU side).
2. Open the tool.
3. Select serial port.
4. Click `Connect`.
5. Mode detection starts automatically.
   - Tool probes DFU automatically at configured probe bauds.
   - If no DFU response is found, tool falls back to normal/runtime baud.
   - If DFU responds to `HELLO`, tool enters `DFU Mode`.
   - If runtime responds with `OK_RUNNING,...`, tool enters `Normal Mode` and shows firmware details.
6. Select firmware `.bin` file.
7. Click `Flash APP Partition`.
8. Wait for `END` success and `REBOOT`.

## Notes

- The DFU target enforces partition bounds and protocol checks.
- If image size exceeds APP partition size reported by `HELLO_RSP`, the tool rejects it.
- The DFU target erases `storage` (LittleFS) upon successful commit.
