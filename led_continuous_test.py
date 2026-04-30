#!/usr/bin/env python3
"""
Continuous LED test — sends TM1815B frames in a tight loop.

Press Enter to START each test, Enter again to STOP.
3-second pause between tests.

Tests focus on speeds and encodings that work for LED 1 (confirmed
at 2.0, 2.4, 3.2 MHz) and adds a 5-bit encoding at 2.0 MHz that
targets the TM1815B's native 400 kHz data rate for proper DO→DIN
forwarding to downstream chips.

Usage:
    python3 led_continuous_test.py
"""

import time
import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4


# --- 4-bit encoding (4 SPI bits per data bit) ---

def _build_lut_4bit():
    """Logic 1 -> 0b0001 (long LOW), Logic 0 -> 0b0111 (short LOW).
    Each data byte -> 4 SPI bytes."""
    lut = []
    for value in range(256):
        encoded = 0
        for bit_pos in range(7, -1, -1):
            if value & (1 << bit_pos):
                encoded = (encoded << 4) | 0b0001
            else:
                encoded = (encoded << 4) | 0b0111
        lut.append(bytes([
            (encoded >> 24) & 0xFF, (encoded >> 16) & 0xFF,
            (encoded >> 8) & 0xFF, encoded & 0xFF,
        ]))
    return lut


LUT_4BIT = _build_lut_4bit()


# --- 5-bit encoding (5 SPI bits per data bit, 400 kHz @ 2.0 MHz) ---

def _build_lut_5bit():
    """Logic 1 -> 0b00001 (3 LOW, 2 HIGH), Logic 0 -> 0b01111 (1 LOW, 4 HIGH).
    Each data byte (8 bits x 5 SPI bits) -> 40 SPI bits = 5 SPI bytes."""
    lut = []
    for value in range(256):
        bits = 0
        for bit_pos in range(7, -1, -1):
            if value & (1 << bit_pos):
                bits = (bits << 5) | 0b00001
            else:
                bits = (bits << 5) | 0b01111
        lut.append(bytes([
            (bits >> 32) & 0xFF, (bits >> 24) & 0xFF,
            (bits >> 16) & 0xFF, (bits >> 8) & 0xFF,
            bits & 0xFF,
        ]))
    return lut


LUT_5BIT = _build_lut_5bit()


# --- 5-bit encoding variant B (wider Logic 1 LOW pulse) ---

def _build_lut_5bit_b():
    """Logic 1 -> 0b00011 (3 LOW, 2 HIGH), Logic 0 -> 0b01111 (1 LOW, 4 HIGH).
    T1l = 1500ns (vs 2000ns in variant A). More centered in 1300-2000ns range."""
    lut = []
    for value in range(256):
        bits = 0
        for bit_pos in range(7, -1, -1):
            if value & (1 << bit_pos):
                bits = (bits << 5) | 0b00011
            else:
                bits = (bits << 5) | 0b01111
        lut.append(bytes([
            (bits >> 32) & 0xFF, (bits >> 24) & 0xFF,
            (bits >> 16) & 0xFF, (bits >> 8) & 0xFF,
            bits & 0xFF,
        ]))
    return lut


LUT_5BIT_B = _build_lut_5bit_b()


def build_frame(r, g, b, w, num_leds, lut, current=10,
                preamble_bytes=80, reset_bytes=80):
    """Build a TM1815B frame using the given LUT encoding."""
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([c1_val ^ 0xFF] * 4)

    buf = bytearray(b'\xFF' * preamble_bytes)

    for bv in c1:
        buf += lut[bv]
    for bv in c2:
        buf += lut[bv]

    for _ in range(num_leds):
        buf += lut[w] + lut[r] + lut[g] + lut[b]

    buf += b'\xFF' * reset_bytes
    return buf


# --- Test configurations ---

TESTS = [
    {
        "name": "4-bit @ 2.0 MHz — RED (confirmed: LED 1 works)",
        "speed": 2_000_000,
        "builder": lambda: build_frame(255, 0, 0, 0, NUM_LEDS, LUT_4BIT,
                                       current=30),
    },
    {
        "name": "5-bit @ 2.0 MHz — RED (400 kHz data rate for forwarding)",
        "speed": 2_000_000,
        "builder": lambda: build_frame(255, 0, 0, 0, NUM_LEDS, LUT_5BIT,
                                       current=30),
    },
    {
        "name": "5-bit-B @ 2.0 MHz — RED (T1l=1500ns, centered timing)",
        "speed": 2_000_000,
        "builder": lambda: build_frame(255, 0, 0, 0, NUM_LEDS, LUT_5BIT_B,
                                       current=30),
    },
    {
        "name": "4-bit @ 2.4 MHz — GREEN (confirmed: LED 1 works)",
        "speed": 2_400_000,
        "builder": lambda: build_frame(0, 255, 0, 0, NUM_LEDS, LUT_4BIT,
                                       current=30),
    },
    {
        "name": "5-bit @ 2.5 MHz — GREEN (500 kHz data rate)",
        "speed": 2_500_000,
        "builder": lambda: build_frame(0, 255, 0, 0, NUM_LEDS, LUT_5BIT,
                                       current=30),
    },
    {
        "name": "4-bit @ 3.2 MHz — BLUE (confirmed: LED 1 works)",
        "speed": 3_200_000,
        "builder": lambda: build_frame(0, 0, 255, 0, NUM_LEDS, LUT_4BIT,
                                       current=30),
    },
    {
        "name": "4-bit @ 1.8 MHz — WHITE (between 1.6 and 2.0)",
        "speed": 1_800_000,
        "builder": lambda: build_frame(0, 0, 0, 255, NUM_LEDS, LUT_4BIT,
                                       current=30),
    },
    {
        "name": "4-bit @ 2.0 MHz — ALL WHITE max current",
        "speed": 2_000_000,
        "builder": lambda: build_frame(255, 255, 255, 255, NUM_LEDS, LUT_4BIT,
                                       current=63),
    },
]


def run_test_loop(test):
    """Send frames continuously until Enter is pressed."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = test["speed"]
    spi.mode = 0b00
    spi.lsbfirst = False

    buf = test["builder"]()
    buf_list = list(buf)
    frame_count = 0
    running = True

    print("           Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait_for_enter():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait_for_enter, daemon=True)
    t.start()

    while running:
        spi.xfer2(buf_list)
        frame_count += 1

    spi.close()
    return frame_count


def main():
    print("=" * 62)
    print("  TM1815B Continuous Test — Encoding & Speed Sweep")
    print(f"  {NUM_LEDS} LEDs, {len(TESTS)} configurations")
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 62)

    for i, test in enumerate(TESTS, 1):
        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        input("           Press Enter to start...")
        sys.stdout.flush()

        frames = run_test_loop(test)
        print(f"           Sent {frames} frames")

        spi = SpiDev()
        spi.open(1, 0)
        spi.max_speed_hz = test["speed"]
        spi.mode = 0b00
        spi.xfer2([0xFF] * 200)
        spi.close()

        time.sleep(3)

    print("\n  Done. Which test changed the LEDs?")
    print("  Key question: did any test light up ALL 4 LEDs?")


if __name__ == "__main__":
    main()
