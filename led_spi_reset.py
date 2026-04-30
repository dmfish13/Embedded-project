#!/usr/bin/env python3
"""
LED SPI1 Reset Tool

Fully resets the SPI1 peripheral and the TM1815B LED chain.
Unbinds and rebinds the spidev driver to clear any stale kernel state,
restores GPIO 20 to SPI1 MOSI, then sends all-off frames to the LEDs.

Requires sudo (for driver unbind/rebind).

Usage:
    sudo python3 led_spi_reset.py
"""

import glob
import os
import subprocess
import time
from pathlib import Path

NUM_LEDS = 8
SPI_SPEED = 2_000_000
PIN = 20

SPIDEV_DRIVER = Path("/sys/bus/spi/drivers/spidev")


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


def build_data_payload():
    c1 = bytes([0x1E, 0x1E, 0x1E, 0x1E])
    c2 = bytes([v ^ 0xFF for v in c1])

    buf = bytearray()
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for _ in range(NUM_LEDS):
        buf += LUT[0] + LUT[0] + LUT[0] + LUT[0]
    return list(buf)


def find_spi1_device():
    """Find the sysfs device name for SPI1.0 (e.g. 'spi1.0')."""
    for path in SPIDEV_DRIVER.glob("spi*"):
        if path.name.startswith("spi1."):
            return path.name
    # Try looking in /sys/bus/spi/devices/ instead
    devices = Path("/sys/bus/spi/devices")
    for path in devices.glob("spi1.*"):
        return path.name
    return None


def reset_spi_driver():
    """Unbind and rebind the spidev driver for SPI1 to clear kernel state."""
    dev_name = find_spi1_device()

    if dev_name:
        print(f"  Unbinding {dev_name} from spidev driver...")
        unbind = SPIDEV_DRIVER / "unbind"
        try:
            unbind.write_text(dev_name)
        except OSError as e:
            print(f"  Unbind note: {e}")
        time.sleep(0.2)

        print(f"  Rebinding {dev_name} to spidev driver...")
        bind = SPIDEV_DRIVER / "bind"
        try:
            bind.write_text(dev_name)
        except OSError as e:
            print(f"  Rebind note: {e}")
        time.sleep(0.2)
    else:
        print("  SPI1 device not found in sysfs, trying dtoverlay reload...")
        subprocess.run(["sudo", "dtoverlay", "-r", "spi1-1cs"],
                       capture_output=True)
        time.sleep(0.5)
        subprocess.run(["sudo", "dtoverlay", "spi1-1cs"],
                       capture_output=True)
        time.sleep(0.5)

    # Verify /dev/spidev1.0 exists
    if os.path.exists("/dev/spidev1.0"):
        print("  /dev/spidev1.0 OK")
    else:
        print("  WARNING: /dev/spidev1.0 not found!")


def main():
    print("Step 1: Resetting SPI1 driver...")
    reset_spi_driver()

    print("Step 2: Restoring GPIO 20 to SPI1 MOSI (alt5)...")
    subprocess.run(["pinctrl", "set", str(PIN), "a5"], capture_output=True)
    time.sleep(0.1)

    from spidev import SpiDev
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False

    print("Step 3: Sending sustained reset (500 ms HIGH)...")
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        spi.xfer2([0xFF] * 500)

    print("Step 4: Sending all-off frames...")
    preamble = [0xFF] * 80
    data = build_data_payload()
    trailing = [0xFF] * 80
    for _ in range(20):
        spi.xfer2(preamble)
        spi.xfer2(data)
        spi.xfer2(trailing)

    spi.close()
    print("Reset complete. LEDs off, SPI1 ready for new commands.")


if __name__ == "__main__":
    main()
