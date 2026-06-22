# OPSUM GUI (Runtime Frontend)

>This tool is to be considered as just an example of how to interact with the core firmware running on the board, and display its readouts on the connected host PC.

This folder contains the source code for the host-side, minimal OPSUM runtime GUI frontend. 

## Run

Install Python dependencies:

```bash
pip install -r src/GUI/opsum-gui/requirements.txt
```

Then run the GUI:

```bash
python src/GUI/opsum-gui/opsum_gui.pyw
```

## Notes

- This GUI is intended for runtime monitoring and interaction.
For firmware updates, the dedicated [DFU flash tool](../flash-tool/) source code is also included.

- The OPSUM protocol is open by design, so [third-party client tools](../../../third_party_tools/) in this repository can be used in place of this.

- Further tools can be freely developed as well, as long as they follow the core [UART communication protocol](../../../protocol-specs/core-uart.md) to talk to the OPSUM board's firmware.