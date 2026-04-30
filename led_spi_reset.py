#!/usr/bin/env python3
"""
LED SPI1 Reset Tool

Sends a long reset pulse followed by a valid all-off frame to force
every TM1815B in the chain out of demo mode and into a known state,
ready to receive new instructions.

Usage:
    python3 led_spi_reset.py
"""

import subprocess
import time
from spidev import SpiDev

NUM_LEDS = 8
SPI_SPEED = 2_000_000
PIN = 20


def encode_byte(value):
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001
        else:
            encoded = (encoded << 4) | 0b0111
    return bytes([
        (encoded >> 24) & 0xFF, (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF, encoded & 0xFF,
    ])


LUT = [encode_byte(v) for v in range(256)]


def build_off_frame():
    """Build a complete all-off frame: reset + C1 + C2 + pixels(0) + reset."""
    c1 = bytes([0x1E, 0x1E, 0x1E, 0x1E])
    c2 = bytes([v ^ 0xFF for v in c1])

    buf = bytearray(b'\xFF' * 2000)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for _ in range(NUM_LEDS):
        buf += LUT[0] + LUT[0] + LUT[0] + LUT[0]
    buf += b'\xFF' * 2000
    return list(buf)


# Step 1: Restore GPIO 20 to SPI1 MOSI alt function
print("Restoring GPIO 20 to SPI1 MOSI (alt5)...")
subprocess.run(["pinctrl", "set", str(PIN), "a5"], capture_output=True)
time.sleep(0.1)

spi = SpiDev()
spi.open(1, 0)
spi.max_speed_hz = SPI_SPEED
spi.mode = 0b00
spi.lsbfirst = False

# Step 2: Hold line HIGH for 500ms to force all chips into reset
print("Sending sustained reset (500 ms HIGH)...")
end = time.monotonic() + 0.5
while time.monotonic() < end:
    spi.xfer2([0xFF] * 500)

# Step 3: Send complete all-off frames to put chips in known state
print("Sending all-off frames...")
frame = build_off_frame()
for i in range(20):
    spi.xfer2(frame)

spi.close()
print("Reset complete. LEDs should be off and ready for new commands.")
