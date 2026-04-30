#!/usr/bin/env python3
"""
LED SPI1 Reset Tool

Restores GPIO 20 to its SPI1 MOSI alt function, reopens the SPI
peripheral, and sends a reset signal to the TM1815B chain. Use this
after led_reset_test.py or any script that reconfigures GPIO 20.

Usage:
    python3 led_spi_reset.py
"""

import subprocess
import time
from spidev import SpiDev

PIN = 20

# Restore GPIO 20 to SPI1 MOSI alt function (a5 on Pi 5)
print("Restoring GPIO 20 to SPI1 MOSI (alt5)...")
subprocess.run(["pinctrl", "set", str(PIN), "a5"], capture_output=True)
time.sleep(0.1)

# Open SPI1 and send reset (sustained HIGH)
print("Sending reset (HIGH >= 200 µs) on SPI1...")
spi = SpiDev()
spi.open(1, 0)
spi.max_speed_hz = 2_000_000
spi.mode = 0b00
spi.xfer2([0xFF] * 500)
spi.close()

print("SPI1 reset complete. LED scripts should work again.")
