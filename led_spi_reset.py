#!/usr/bin/env python3
"""
LED SPI1 Reset Tool

Fully resets the SPI1 peripheral by killing processes holding the
device, removing and re-adding the device tree overlay, and reloading
the spidev kernel module. Then sends all-off frames to the LEDs and
drives GPIO 20 LOW to cut parasitic power.

Requires sudo.

Usage:
    sudo python3 led_spi_reset.py
"""

import os
import subprocess
import time

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


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def main():
    # Step 1: Kill any process holding /dev/spidev1.0 open
    print("Step 1: Killing processes using /dev/spidev1.0...")
    result = run(["fuser", "-k", "/dev/spidev1.0"])
    if result.returncode == 0:
        print("  Killed stale process(es)")
        time.sleep(0.5)
    else:
        print("  No processes using the device")

    # Step 2: Remove the SPI1 device tree overlay
    print("Step 2: Removing SPI1 overlay...")
    result = run(["dtoverlay", "-r", "spi1-1cs"])
    if result.returncode == 0:
        print("  Overlay removed")
    else:
        print(f"  Note: {result.stderr.strip() or 'overlay may not be loaded'}")
    time.sleep(0.3)

    # Step 3: Reload spidev kernel module
    print("Step 3: Reloading spidev kernel module...")
    run(["rmmod", "spidev"])
    time.sleep(0.2)
    run(["modprobe", "spidev"])
    time.sleep(0.2)
    print("  spidev reloaded")

    # Step 4: Re-add the SPI1 device tree overlay
    print("Step 4: Re-adding SPI1 overlay...")
    result = run(["dtoverlay", "spi1-1cs"])
    if result.returncode == 0:
        print("  Overlay added")
    else:
        print(f"  Error: {result.stderr.strip()}")
    time.sleep(0.5)

    # Verify device exists
    if os.path.exists("/dev/spidev1.0"):
        print("  /dev/spidev1.0 OK")
    else:
        print("  WARNING: /dev/spidev1.0 not found! Check dtoverlay config.")
        return

    # Step 5: Restore GPIO 20 to SPI1 MOSI
    print("Step 5: Restoring GPIO 20 to SPI1 MOSI (alt5)...")
    run(["pinctrl", "set", str(PIN), "a5"])
    time.sleep(0.1)

    # Step 6: Open SPI and send reset + all-off frames
    print("Step 6: Sending reset and all-off frames to LEDs...")
    from spidev import SpiDev
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False

    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        spi.xfer2([0xFF] * 500)

    preamble = [0xFF] * 80
    data = build_data_payload()
    trailing = [0xFF] * 80
    for _ in range(20):
        spi.xfer2(preamble)
        spi.xfer2(data)
        spi.xfer2(trailing)

    spi.close()

    # Step 7: Drive GPIO 20 LOW to cut parasitic power
    print("Step 7: Setting GPIO 20 LOW...")
    run(["pinctrl", "set", str(PIN), "op", "dl"])

    print("Reset complete. LEDs off, GPIO 20 LOW.")


if __name__ == "__main__":
    main()
