#!/usr/bin/env python3
"""
Single-frame LED test — sends ONE frame at a time via SPI.

No GPIO mode switching — uses only SPI transfers.

Usage:
    python3 led_single_frame_test.py
"""

import time
from spidev import SpiDev

NUM_LEDS = 4
SPI_SPEED = 2_000_000


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


def build_frame(r, g, b, w, num_leds, current=30):
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


def send_frame(r, g, b, w, current=30, label=""):
    """Send a single TM1815B frame via SPI."""
    buf = build_frame(r, g, b, w, NUM_LEDS, current=current)

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False
    spi.xfer2(list(buf))
    spi.close()

    if label:
        print(f"  Sent: {label}  (RGBW={r},{g},{b},{w}  current={current})")


def main():
    print("=" * 55)
    print("  Single-Frame LED Test")
    print(f"  {NUM_LEDS} LEDs, TM1815B @ {SPI_SPEED/1e6:.1f} MHz")
    print("=" * 55)

    input("\n  Press Enter to begin...\n")

    tests = [
        ("All WHITE — max current", 255, 255, 255, 255, 63),
        ("All WHITE — current=30",  255, 255, 255, 255, 30),
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


if __name__ == "__main__":
    main()
