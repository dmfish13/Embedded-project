#!/usr/bin/env python3
"""
Single-frame LED test — sends ONE frame, then holds GPIO 20 HIGH.

Unlike the continuous test, this avoids inter-transfer MOSI glitches
by switching GPIO 20 to output-HIGH after the SPI transfer, ensuring
the TM1815B sees a clean >= 200 µs reset to latch the data.

Usage:
    python3 led_single_frame_test.py
"""

import time
import subprocess
from spidev import SpiDev

NUM_LEDS = 4


def encode_byte(value):
    """TM1815B: Logic 1 -> 0b0001 (long LOW), Logic 0 -> 0b0111 (short LOW)."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001
        else:
            encoded = (encoded << 4) | 0b0111
    return bytes([
        (encoded >> 24) & 0xFF,
        (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF,
        encoded & 0xFF,
    ])


LUT = [encode_byte(v) for v in range(256)]


def build_frame(r, g, b, w, num_leds, current=10):
    """Build one complete TM1815B frame.

    Structure: [preamble 0xFF] [C1] [C2] [D1..Dn] [trailing 0xFF reset]
    """
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([c1_val ^ 0xFF] * 4)

    buf = bytearray(b'\xFF' * 80)

    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]

    for _ in range(num_leds):
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]

    buf += b'\xFF' * 100

    return buf


def gpio_set_high(pin=20):
    """Configure GPIO pin as output HIGH using pinctrl (Pi 5)."""
    subprocess.run(["pinctrl", "set", str(pin), "op", "dh"],
                   capture_output=True)


def gpio_release(pin=20):
    """Release GPIO pin back to alt function for SPI."""
    subprocess.run(["pinctrl", "set", str(pin), "a5"],
                   capture_output=True)


def send_frame(r, g, b, w, current=10, label=""):
    """Send a single TM1815B frame and hold the line HIGH afterward."""
    buf = build_frame(r, g, b, w, NUM_LEDS, current=current)

    gpio_release(20)

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = 1_600_000
    spi.mode = 0b00
    spi.lsbfirst = False

    spi.xfer2(list(buf))

    spi.close()

    gpio_set_high(20)

    if label:
        print(f"  Sent: {label}  (RGBW={r},{g},{b},{w}  current={current})")


def main():
    print("=" * 55)
    print("  Single-Frame LED Test")
    print(f"  {NUM_LEDS} LEDs, TM1815B @ 1.6 MHz")
    print("=" * 55)

    gpio_set_high(20)
    time.sleep(0.5)

    input("\n  Press Enter to begin...\n")

    tests = [
        ("All WHITE — max current", 255, 255, 255, 255, 63),
        ("All WHITE — current=10",  255, 255, 255, 255, 10),
        ("Pure RED",                255, 0,   0,   0,   30),
        ("Pure GREEN",              0,   255, 0,   0,   30),
        ("Pure BLUE",               0,   0,   255, 0,   30),
        ("Pure W channel only",     0,   0,   0,   255, 30),
        ("All OFF",                 0,   0,   0,   0,   10),
    ]

    for label, r, g, b, w, current in tests:
        send_frame(r, g, b, w, current=current, label=label)
        time.sleep(5)

    print("\n  Test complete.")
    print("  If no colors appeared, try running the continuous version")
    print("  to confirm the signal still stops cycling:")
    print("    python3 led_continuous_test.py")


if __name__ == "__main__":
    main()
