from machine import Pin, UART, I2C
import neopixel
import time, struct
import ina226
import config
import gc
import machine

FIRMWARE_NAME="OPSUM Core Firmware"
VERSION = "0.1"
TARGET_BOARD_REV = "A"
FIRMWARE_STRING = f"{FIRMWARE_NAME},{VERSION},{TARGET_BOARD_REV},{config.BOARD}"

# Named color mapping
NAMED_COLORS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "cyan": (0, 255, 255),
    "white": (255, 255, 255),
    "pink": (255,20,147),
}

def set_internal_led_color(name=None, brightness=1.0):
    """Set ESP32-S3 onboard led (type "WS2812" on pin ) color using NeoPixel.

    If `name` is provided, set the LED to that named color and return.
    If no name is provided, turn off the LED.
    """
    if name:
        rgb = NAMED_COLORS.get(name.lower())
        if rgb:
            # Apply brightness scaling
            scaled_rgb = tuple(int(c * brightness) for c in rgb)
            # print(f"Setting color '{name}' (brightness: {int(brightness * 100)}%): RGB{scaled_rgb}")
            led[0] = scaled_rgb
            led.write()
        else:
            print(f"Color name '{name}' not found.")
        return

    # Turn off LED
    led[0] = (0, 0, 0)
    led.write()


def poll_uart_commands():
    global script_running
    if not uart.any():
        return

    data = uart.read()
    if data is None:
        return

    if b"STATUS" in data:
        uart.write(f"\r\nOK_RUNNING,{FIRMWARE_STRING}\r\n")

    elif b"ENTERDFU" in data:
        # Enter DFU app by selecting the dedicated OTA app partition in otadata.
        try:
            import esp32

            dfu_parts = []
            try:
                dfu_parts = esp32.Partition.find(label="dfu")
            except Exception:
                dfu_parts = []

            if not dfu_parts:
                uart.write("\r\nERR_DFU_PARTITION_NOT_FOUND\r\n")
                return

            dfu_parts[0].set_boot()
            uart.write("\r\nOK_REBOOTING_INTO_DFU_MODE\r\n")
            time.sleep_ms(120)
            machine.reset()
        except Exception as exc:
            print(f"ENTERDFU failed: {exc}")
            uart.write("\r\nERR_DFU_BOOT_TARGET_SET_FAILED\r\n")

    elif b"STOP" in data:
        if script_running:
            uart.write("\r\nOK_STOPPING_MAIN_LOOP\r\n")
            script_running = False

# initialize NeoPixel LED, PROBE+ pin, UART, and I2C
led = neopixel.NeoPixel(Pin(config.NEOPIXEL_PIN), 1)
probe = Pin(config.PROBE_PIN, Pin.IN, Pin.PULL_UP) if config.PROBE_PIN else None
uart = UART(config.UART_ID, baudrate=115200, tx=Pin(config.UART_TX_PIN), rx=Pin(config.UART_RX_PIN))
i2c = I2C(config.I2C_ID, scl=Pin(config.INA_SCL_PIN), sda=Pin(config.INA_SDA_PIN))

# Initialize INA226 sensor over I2C
ina = ina226.INA226(i2c, addr=config.INA226_I2C_ADDRESS)
ina.configure(
    # avg=ina226.INA226.AVG_512, # default
    # avg=ina226.INA226.AVG_16, # faster response
    avg=ina226.INA226.AVG_1, # fastest response (no averaging in firmware, leave that to software side)
    vbusct=ina226.INA226.VBUSCT_588US,
    vshct=ina226.INA226.VSHCT_588US,
    mode=ina226.INA226.MODE_SHUNT_BUS_CONTINUOUS,
)
ina.calibrate(r_shunt_ohms=config.SHUNT_RESISTOR_OHMS, max_expected_amps=config.MAX_EXPECTED_CURRENT_AMPS)

# main loop
ledfadeout = False
script_running = True
try:
    while script_running:
        poll_uart_commands()

        #* Pack data as binary with 2-byte start marker, seq and checksum
        #* Frame layout (little-endian):
        #* - 2 bytes: start marker 0xAA 0x55 (sent as uint16 0x55AA little-endian)
        #* - 1 byte : event type (0x73)
        #* - 1 byte : seq (0..255)
        #* - 4 bytes: timestamp (uint32)
        #* - 4 bytes: bus voltage (float32)
        #* - 4 bytes: current (float32)
        #* - 4 bytes: shunt voltage (float32)
        #* - 4 bytes: power (float32)
        #* - 1 byte : checksum (XOR over bytes from event..power)
        #* - 2 bytes: end marker 0x66 0xBB (sent as uint16 0x66BB little-endian)
        #* Total: 27 bytes
        try:
            if 'seq' not in globals():
                seq = 0
        except Exception:
            seq = 0
        # build payload (event, seq, timestamp, voltage, current, shunt voltage, power)
        ev = 0x73
        ts = int(time.ticks_ms()) & 0xFFFFFFFF
        payload = struct.pack('<BBIffff', ev, seq, ts, ina.bus_voltage, ina.current, ina.shunt_voltage, ina.power)
        # compute simple XOR checksum over payload (event..power)
        checksum = 0
        for b in payload:
            checksum ^= b
        # define start and end markers
        start = struct.pack('<H', 0x55AA)
        end = struct.pack('<H', 0x66BB)
        # construct full frame (start marker + payload + checksum + end marker)
        frame = start + payload + struct.pack('<B', checksum) + end
        try:
            uart.write(frame)
        except Exception:
            # best-effort: try writing in two parts
            try:
                uart.write(start + payload + struct.pack('<B', checksum))
                uart.write(end)
            except Exception:
                pass
        # advance sequence number
        seq = (seq + 1) & 0xFF
        if seq == 0 or seq == 128:
            ledfadeout = not ledfadeout
        if ledfadeout:
            ledbrightness = 1 - (seq / 255)
        else:
            ledbrightness = seq / 255
        if probe and not probe.value():
            ledcolor = config.ledcolor_probemode
        else:
            ledcolor = config.ledcolor_normalmode
        set_internal_led_color(ledcolor, brightness=ledbrightness) # vary brightness with sequence number for visual feedback
        time.sleep_ms(10)
        #print(f"[{config.BOARD}] Sent frame: event={ev}, seq={seq}, ts={ts}, voltage={ina.bus_voltage:.6f}V, current={ina.current:.6f}A, shunt={ina.shunt_voltage:.6f}V, power={ina.power:.6f}W")
except KeyboardInterrupt:
    pass
finally:
    # This block executes when the loop finishes or if the code crashes
    set_internal_led_color() # Ensure LED is turned off on exit

    # Flush the lines completely
    time.sleep_ms(100)
    while uart.any():
        uart.read()
        
    # Tell MicroPython to link this UART to the system REPL (running on UART0)
    # to allow accessing it over the same USB port after the script exits
    import os
    os.dupterm(uart, 0)
