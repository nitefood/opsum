# OPSUM Core UART Protocol

Host-to-runtime protocol for command/control and runtime-to-host binary telemetry.

## Scope

- Defines command tokens accepted by runtime firmware.
- Defines binary telemetry frame format emitted by runtime firmware.
- Covers mixed text and binary traffic sharing the same UART stream.

## Transport

- UART settings: `115200`, `8N1`.
- Device-side pins by board:
  - ESP32-S3: TX=GPIO3, RX=GPIO4
  - ESP32-C3: TX=GPIO2, RX=GPIO3
- Commands are plain ASCII byte tokens.
- Telemetry is fixed-size binary framing.

## Runtime Command Channel

Runtime firmware reads raw UART bytes and searches for command tokens.

Supported tokens:

- `STATUS`
- `ENTERDFU`
- `STOP`

### Command Matching Rules

- Token detection is substring-based (`b"TOKEN" in data`), not line-based parsing.
- If multiple tokens appear in one read buffer, only the first matching branch is processed in this priority order:
  1. `STATUS`
  2. `ENTERDFU`
  3. `STOP`
- Tokens are case-sensitive.

### Command Responses

All command responses are ASCII text framed by CRLF.

#### `STATUS`

Response format:

```text
\r\nOK_RUNNING,{FIRMWARE_NAME},{VERSION},{TARGET_BOARD_REV},{BOARD}\r\n
```

#### `ENTERDFU`

Behavior:

- Looks up partition labeled `dfu`.
- Sets boot target to `dfu` partition.
- Sends success text.
- Waits briefly, then resets MCU.

Possible responses:

- Success: `\r\nOK_REBOOTING_INTO_DFU_MODE\r\n`
- Error: `\r\nERR_DFU_PARTITION_NOT_FOUND\r\n`
- Error: `\r\nERR_DFU_BOOT_TARGET_SET_FAILED\r\n`

#### `STOP`

Behavior:

- Stops the runtime main loop.

Response:

- `\r\nOK_STOPPING_MAIN_LOOP\r\n`

## Runtime Telemetry Channel

Runtime firmware emits one binary telemetry frame per loop iteration.

- Nominal loop delay: `10 ms`.
- Nominal telemetry rate: about `100 Hz`.
- Frame endianness: little-endian for all multi-byte fields.

### Frame Layout

```text
START[2] EVENT[1] SEQ[1] TS_MS[4] VBUS[4] CURRENT[4] VSHUNT[4] POWER[4] XOR[1] END[2]
```

Total frame size is exactly `27` bytes.

### Field Definitions

- `START`: constant marker `0xAA55` on the wire (`AA 55`).
- `EVENT`: event code. Currently only the *sample* event code `0x73` (ASCII letter 's') is supported.
- `SEQ`: unsigned 8-bit sequence counter (`0..255`, wraps).
- `TS_MS`: `uint32` millisecond tick counter (`time.ticks_ms()` masked to 32-bit).
- `VBUS`: `float32` bus voltage.
- `CURRENT`: `float32` current.
- `VSHUNT`: `float32` shunt voltage.
- `POWER`: `float32` power.
- `XOR`: checksum byte; XOR over payload bytes from `EVENT` through `POWER`.
- `END`: constant marker `0x66BB` on the wire (`BB 66`).

### Firmware Packing Model

```text
payload = struct.pack('<BBIffff', event, seq, ts, vbus, current, vshunt, power)
frame = start + payload + checksum + end
```

Where:

- `start = struct.pack('<H', 0x55AA)` resulting in wire bytes `AA 55`.
- `end = struct.pack('<H', 0x66BB)` resulting in wire bytes `BB 66`.

## Host Parsing Requirements

- Continuously scan stream for `AA 55` start marker.
- Once aligned, read fixed-length `27` bytes.
- Validate end marker and XOR checksum before accepting the sample.
- If validation fails, advance by one byte and resynchronize.
- Treat telemetry and text responses as the same physical UART stream.

## Stream Multiplexing Behavior

Binary telemetry and text command responses share one UART link.

- While runtime is streaming telemetry, command responses may appear between frames.
- Host implementations should run a framing parser for telemetry and a side text extractor for CRLF-delimited responses.

## Runtime Exit Behavior

When main loop stops (for example after `STOP`), firmware performs cleanup and may stop binary telemetry.

## Canonical Source

Reference implementation: [../src/Firmware/Core/main.py](../src/Firmware/Core/main.py)
