#!/usr/bin/env python3
"""
White LED position finder — determines which byte position in the
TM1815B pixel data and C1 current register controls the white LED.

Sends a single non-zero value in each of the 4 byte positions,
one at a time, so you can observe which position activates which
LED (RGB red, green, blue, or white diode).

Also tests C1 current register positions independently.

Usage:
    python3 led_white_position_test.py
"""

import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4
SPI_SPEED = 2_000_000


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


def build_frame(d0, d1, d2, d3, c0, c1_val, c2_val, c3):
    """Build frame with explicit control over all 4 byte positions.

    Pixel data bytes: d0, d1, d2, d3  (per LED, repeated for all LEDs)
    C1 current bytes: c0, c1_val, c2_val, c3  (6-bit each, bits 7:6 = 0)
    """
    c1 = bytes([c0 & 0x3F, c1_val & 0x3F, c2_val & 0x3F, c3 & 0x3F])
    c2 = bytes([v ^ 0xFF for v in c1])

    buf = bytearray(b'\xFF' * 80)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for _ in range(NUM_LEDS):
        buf += LUT[d0] + LUT[d1] + LUT[d2] + LUT[d3]
    buf += b'\xFF' * 80
    return buf


def run_test(buf_list):
    """Send continuously until Enter is pressed."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False

    frame_count = 0
    running = True

    print("         Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait, daemon=True)
    t.start()

    while running:
        spi.xfer2(buf_list)
        frame_count += 1

    spi.close()
    return frame_count


TESTS = [
    # =====================================================
    # SECTION 1: Pixel data — one position at a time
    # Current code assumes order: [W, R, G, B]
    # If the white LED only responds to a specific position,
    # that tells us where W actually is.
    # =====================================================
    {
        "section": "\n  === SECTION 1: Pixel data byte positions (one at a time) ===",
        "name": "Byte 0 = 255, rest = 0  (should be W if order is WRGB)",
        "d": [255, 0, 0, 0],
        "c": [30, 30, 30, 30],
    },
    {
        "name": "Byte 1 = 255, rest = 0  (should be R if order is WRGB)",
        "d": [0, 255, 0, 0],
        "c": [30, 30, 30, 30],
    },
    {
        "name": "Byte 2 = 255, rest = 0  (should be G if order is WRGB)",
        "d": [0, 0, 255, 0],
        "c": [30, 30, 30, 30],
    },
    {
        "name": "Byte 3 = 255, rest = 0  (should be B if order is WRGB)",
        "d": [0, 0, 0, 255],
        "c": [30, 30, 30, 30],
    },

    # =====================================================
    # SECTION 2: All pixel bytes = 255, vary current per position
    # Only one C1 position has current enabled (30), rest = 0.
    # This tests whether the white LED needs its current in a
    # specific C1 position to turn on.
    # =====================================================
    {
        "section": "\n  === SECTION 2: C1 current — one position enabled ===",
        "name": "All PWM=255, only C1 byte 0 has current (30)",
        "d": [255, 255, 255, 255],
        "c": [30, 0, 0, 0],
    },
    {
        "name": "All PWM=255, only C1 byte 1 has current (30)",
        "d": [255, 255, 255, 255],
        "c": [0, 30, 0, 0],
    },
    {
        "name": "All PWM=255, only C1 byte 2 has current (30)",
        "d": [255, 255, 255, 255],
        "c": [0, 0, 30, 0],
    },
    {
        "name": "All PWM=255, only C1 byte 3 has current (30)",
        "d": [255, 255, 255, 255],
        "c": [0, 0, 0, 30],
    },

    # =====================================================
    # SECTION 3: Test alternate byte orders for pixel data
    # Maybe the real order is RGBW, GRBW, or BRGW.
    # Send a known color (red=255) in what we think is
    # the R position. If a different color appears, the
    # order is wrong.
    # =====================================================
    {
        "section": "\n  === SECTION 3: Order confirmation — 255 in each slot ===",
        "name": "Byte order test: [255, 128, 64, 32]",
        "d": [255, 128, 64, 32],
        "c": [30, 30, 30, 30],
    },
    {
        "name": "Byte order test: [32, 64, 128, 255]",
        "d": [32, 64, 128, 255],
        "c": [30, 30, 30, 30],
    },

    # =====================================================
    # SECTION 4: High current on W position candidates
    # Maybe the white LED needs more current than 30.
    # Test each position with max current (63) and full PWM.
    # =====================================================
    {
        "section": "\n  === SECTION 4: Max current (63) per position, one at a time ===",
        "name": "Byte 0 = 255, current 0 = 63 (rest off)",
        "d": [255, 0, 0, 0],
        "c": [63, 0, 0, 0],
    },
    {
        "name": "Byte 1 = 255, current 1 = 63 (rest off)",
        "d": [0, 255, 0, 0],
        "c": [0, 63, 0, 0],
    },
    {
        "name": "Byte 2 = 255, current 2 = 63 (rest off)",
        "d": [0, 0, 255, 0],
        "c": [0, 0, 63, 0],
    },
    {
        "name": "Byte 3 = 255, current 3 = 63 (rest off)",
        "d": [0, 0, 0, 255],
        "c": [0, 0, 0, 63],
    },

    # =====================================================
    # SECTION 5: All channels off (baseline)
    # =====================================================
    {
        "section": "\n  === SECTION 5: All off ===",
        "name": "All PWM = 0, all current = 0",
        "d": [0, 0, 0, 0],
        "c": [0, 0, 0, 0],
    },
]


def main():
    print("=" * 62)
    print("  TM1815B White LED Position Finder")
    print(f"  {NUM_LEDS} LEDs @ {SPI_SPEED/1e6:.1f} MHz SPI")
    print()
    print("  For each test, note which LEDs activate:")
    print("    - RGB diode: which color? (Red / Green / Blue)")
    print("    - White diode: on or off?")
    print()
    print("  Frame: [preamble] [C1: c0,c1,c2,c3] [C2] [D: d0,d1,d2,d3] [reset]")
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 62)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        d = test["d"]
        c = test["c"]
        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         D bytes:  [{d[0]:>3}, {d[1]:>3}, {d[2]:>3}, {d[3]:>3}]")
        print(f"         C1 curr:  [{c[0]:>3}, {c[1]:>3}, {c[2]:>3}, {c[3]:>3}]")

        input("         Press Enter to start...")

        buf = build_frame(d[0], d[1], d[2], d[3], c[0], c[1], c[2], c[3])
        frames = run_test(list(buf))
        print(f"         Sent {frames} frames")

    print("\n  Done. Use the results to fill in this map:")
    print("    Byte 0 = ___  (W / R / G / B)")
    print("    Byte 1 = ___  (W / R / G / B)")
    print("    Byte 2 = ___  (W / R / G / B)")
    print("    Byte 3 = ___  (W / R / G / B)")
    print()
    print("  Key questions:")
    print("    1. Did any position activate the white LED diode?")
    print("    2. If not, did enabling current in a specific C1")
    print("       position (Section 2) make a difference?")
    print("    3. What is the actual byte order for RGB?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
