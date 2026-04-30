#!/usr/bin/env python3
"""
LED Protocol Tester — tries multiple encodings to find the right one.

Sends all-white to 4 LEDs using 6 different protocol configurations,
pausing 4 seconds between each. Watch the LEDs and note which attempt
(if any) produces white light instead of the cycling pattern.

Usage:
    python3 led_protocol_test.py
"""

import subprocess
import time
from spidev import SpiDev

subprocess.run(["pinctrl", "set", "20", "a5"], capture_output=True)

NUM_LEDS = 4


def make_spi(speed):
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = speed
    spi.mode = 0b00
    spi.lsbfirst = False
    return spi


# ---- Encoding A: 6.5 MHz, 8 SPI bits per data bit (from Cycling.py) ----

def encode_6m5(value):
    """Each data bit becomes one SPI byte: 0xFC for 1, 0xC0 for 0."""
    result = bytearray(8)
    for i in range(8):
        result[7 - i] = 0xFC if (value >> i) & 1 else 0xC0
    return bytes(result)


def build_6m5_grbw(r, g, b, w, num):
    lut = [encode_6m5(v) for v in range(256)]
    buf = bytearray(b'\x00' * 42)  # preamble/reset
    for _ in range(num):
        buf += lut[g] + lut[r] + lut[b] + lut[w]
    buf += b'\x00' * 42  # trailing reset
    return buf


def build_6m5_rgbw(r, g, b, w, num):
    lut = [encode_6m5(v) for v in range(256)]
    buf = bytearray(b'\x00' * 42)
    for _ in range(num):
        buf += lut[r] + lut[g] + lut[b] + lut[w]
    buf += b'\x00' * 42
    return buf


def build_6m5_wrgb(r, g, b, w, num):
    lut = [encode_6m5(v) for v in range(256)]
    buf = bytearray(b'\x00' * 42)
    for _ in range(num):
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\x00' * 42
    return buf


# ---- Encoding B: 2.4 MHz, 3 SPI bits per data bit ----

def encode_2m4(value):
    """Each data bit becomes 3 SPI bits: 0b110 for 1, 0b100 for 0."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 3) | 0b110
        else:
            encoded = (encoded << 3) | 0b100
    return bytes([(encoded >> 16) & 0xFF, (encoded >> 8) & 0xFF, encoded & 0xFF])


def build_2m4_grbw(r, g, b, w, num):
    lut = [encode_2m4(v) for v in range(256)]
    buf = bytearray()
    for _ in range(num):
        buf += lut[g] + lut[r] + lut[b] + lut[w]
    buf += b'\x00' * 30
    return buf


# ---- Encoding C: 1.6 MHz, 4 SPI bits per data bit, inverted (TM1815B) ----

def encode_inv(value):
    """TM1815B: Logic 1 -> 0b0001 (long LOW), Logic 0 -> 0b0111 (short LOW)."""
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


def build_tm1815b_wrgb(r, g, b, w, num, current=10):
    lut = [encode_inv(v) for v in range(256)]
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([b ^ 0xFF for b in c1])
    buf = bytearray(b'\xFF' * 80)  # idle-HIGH preamble (400 µs >= 280 µs reset)
    for byte_val in c1:
        buf += lut[byte_val]
    for byte_val in c2:
        buf += lut[byte_val]
    for _ in range(num):
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * 60  # idle-HIGH reset
    return buf


def build_tm1815b_no_c1c2(r, g, b, w, num):
    """TM1815B encoding but without C1/C2 header."""
    lut = [encode_inv(v) for v in range(256)]
    buf = bytearray(b'\xFF' * 80)
    for _ in range(num):
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * 60
    return buf


# ---- Test runner ----

TESTS = [
    {
        "name": "6.5 MHz / 8-bit / GRBW (SK6812 from Cycling.py)",
        "speed": 6_500_000,
        "builder": lambda: build_6m5_grbw(255, 255, 255, 255, NUM_LEDS),
    },
    {
        "name": "6.5 MHz / 8-bit / RGBW",
        "speed": 6_500_000,
        "builder": lambda: build_6m5_rgbw(255, 255, 255, 255, NUM_LEDS),
    },
    {
        "name": "6.5 MHz / 8-bit / WRGB",
        "speed": 6_500_000,
        "builder": lambda: build_6m5_wrgb(255, 255, 255, 255, NUM_LEDS),
    },
    {
        "name": "2.4 MHz / 3-bit / GRBW (SK6812 standard)",
        "speed": 2_400_000,
        "builder": lambda: build_2m4_grbw(255, 255, 255, 255, NUM_LEDS),
    },
    {
        "name": "1.6 MHz / 4-bit inverted / WRGB + C1/C2 (TM1815B)",
        "speed": 1_600_000,
        "builder": lambda: build_tm1815b_wrgb(255, 255, 255, 255, NUM_LEDS),
    },
    {
        "name": "1.6 MHz / 4-bit inverted / WRGB no C1/C2",
        "speed": 1_600_000,
        "builder": lambda: build_tm1815b_no_c1c2(255, 255, 255, 255, NUM_LEDS),
    },
]


def main():
    print("=" * 55)
    print("  LED Protocol Tester")
    print(f"  Sending all-white to {NUM_LEDS} LEDs")
    print("  Watch the LEDs — note which test produces white")
    print("=" * 55)

    input("\n  Press Enter to begin...\n")

    for i, test in enumerate(TESTS, 1):
        print(f"  [{i}/{len(TESTS)}] {test['name']}")
        buf = test["builder"]()
        print(f"         {len(buf)} bytes")

        spi = make_spi(test["speed"])

        # Send the frame 3 times rapidly to ensure latch
        for _ in range(3):
            spi.xfer2(list(buf))
            time.sleep(0.01)

        spi.close()

        print(f"         Watching for 4 seconds...")
        time.sleep(4)
        print()

    print("  Done. Which test (if any) turned the LEDs white?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        subprocess.run(["pinctrl", "set", "20", "op", "dl"], capture_output=True)
