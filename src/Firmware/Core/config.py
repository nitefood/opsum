import sys

# Detect platform
machine_info = sys.implementation._machine

# Define Pin Dictionary
if "ESP32C3" in machine_info:
    # C3 Configuration
    BOARD = "C3"
    UART_ID = 1
    UART_TX_PIN = 2
    UART_RX_PIN = 3
    I2C_ID = 0
    INA_SCL_PIN = 8
    INA_SDA_PIN = 7
    NEOPIXEL_PIN = 10
    PROBE_PIN = False # unsupported on C3
else:
    # Default to S3 Configuration
    BOARD = "S3"
    UART_ID = 2
    UART_TX_PIN = 3
    UART_RX_PIN = 4
    I2C_ID = 1
    INA_SCL_PIN = 9
    INA_SDA_PIN = 8
    NEOPIXEL_PIN = 21
    PROBE_PIN = 10

# Common constants
SHUNT_RESISTOR_OHMS = 0.003
MAX_EXPECTED_CURRENT_AMPS = 20
INA226_I2C_ADDRESS = 0x40
ledcolor_normalmode = "cyan"
ledcolor_probemode = "magenta"