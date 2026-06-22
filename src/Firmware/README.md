# Firmware Overview

This folder contains all firmware (board-side) components.

Communication Protocol references:

- [DFU Mode Communication Protocol](../../protocol-specs/dfu-uart.md)
- [Core Firmware Communication Protocol](../../protocol-specs/core-uart.md)

## Subfolders

- [Core](Core/): runtime application firmware (*written in MicroPython*).
- [DFU](DFU/): UART-based DFU updater firmware (*written in C using the ESP-IDF framework*).

## Typical Workflow

1. Program DFU firmware on a board during initial setup.
2. Program runtime firmware (`Core`) as initial app image.
3. For future updates, use the [flash tool](../GUI/flash-tool/) to push new app images over DFU.

This flow is designed so you can build and maintain your own board without vendor-locked tooling.

