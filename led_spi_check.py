#!/usr/bin/env python3
"""
SPI1 signal path check.

Sends sustained 0xFF bytes (all HIGH) on SPI1 MOSI for 5 seconds.
If the LED cycling stops, SPI1 is reaching the data line.
If cycling continues, there is a wiring or config issue between
SPI1 MOSI (GPIO 20) and the LED data-in pin.
"""

import subprocess
import time
from spidev import SpiDev

subprocess.run(["pinctrl", "set", "20", "a5"], capture_output=True)

try:
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = 1_000_000
    spi.mode = 0b00

    print("Sending sustained HIGH on SPI1 MOSI for 5 seconds...")
    print("Watch the LEDs — if cycling stops, SPI1 is connected.")
    print()

    start = time.monotonic()
    while time.monotonic() - start < 5:
        spi.xfer2([0xFF] * 256)

    spi.close()
    print("Done. Did the cycling stop?")
except KeyboardInterrupt:
    print("\n  Interrupted.")
finally:
    subprocess.run(["pinctrl", "set", "20", "op", "dl"], capture_output=True)
