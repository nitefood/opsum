# Core Firmware (Runtime App)

This folder contains the main runtime firmware logic.

Main entry file:

- `main.py`

Protocol reference:

- [core-uart.md](../../../protocol-specs/core-uart.md)

## Runtime UART Commands

Current control commands handled by runtime firmware:

- `STATUS`
  - replies with the current firmware and board hardware info to the host
  - Response format:
    - `OK_RUNNING,{FIRMWARE_NAME},{VERSION},{TARGET_BOARD_REV},{BOARD}`

- `ENTERDFU`
  - Sets boot target to DFU app partition and reboots.

- `STOP`
  - Stops the main firmware loop (useful for stopping the firmware loop and breaking into the MicroPython REPL while debugging live firmware changes).

## Purpose

The runtime app provides board functionality and telemetry streaming. It can hand off control to DFU mode for firmware updates.

