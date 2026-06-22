# manifest.py
metadata(version="1.0")

# This manifest file specifies the files to be included in the firmware build for the ESP32-S3.
require("neopixel")
freeze(".", "config.py")
freeze(".", "main.py")
freeze(".", "ina226.py")