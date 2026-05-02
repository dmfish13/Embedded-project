#!/usr/bin/env python3
"""
C1/C2 current register test — systematically tests the C1 and C2
constant-current command bytes to verify proper encoding and
TM1815B reception.

Tests different current values, verifies C2 = ~C1 requirement,
and checks if incorrect C2 prevents data decoding.

Setup: Pi → level shifter → PCB1 → PCB2 → PCB3 → PCB4 (4 LEDs)

Usage:
    python3 led_c1c2_test.py
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


def build_frame_raw(c1_bytes, c2_bytes, pixels):
    """Build frame with explicit C1 and C2 byte values.

    c1_bytes: 4 raw bytes for C1 [W, R, G, B]
    c2_bytes: 4 raw bytes for C2 [W, R, G, B]
    pixels:   list of (W, R, G, B) tuples for pixel data
    """
    buf = bytearray(b'\xFF' * 80)
    for bv in c1_bytes:
        buf += LUT[bv]
    for bv in c2_bytes:
        buf += LUT[bv]
    for w, r, g, b in pixels:
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
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


RED_ALL = [(0, 255, 0, 0)] * NUM_LEDS
WHITE_ALL = [(255, 0, 0, 0)] * NUM_LEDS
FULL_ALL = [(255, 255, 255, 255)] * NUM_LEDS

TESTS = [
    # =====================================================
    # SECTION 1: Valid C1/C2 with different current levels
    # C2 = bitwise NOT of C1. All pixel data = RED.
    # Bits 7,6 of each C1 byte must be 0.
    # =====================================================
    {
        "section": "\n  === SECTION 1: Valid C1/C2, varying current ===",
        "name": "Current = 30 (0x1E), all RED",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": RED_ALL,
    },
    {
        "name": "Current = 0 (minimum 6.5mA), all RED",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": RED_ALL,
    },
    {
        "name": "Current = 63 (0x3F, max 38mA), all RED",
        "c1": [0x3F, 0x3F, 0x3F, 0x3F],
        "c2": [0xC0, 0xC0, 0xC0, 0xC0],
        "pixels": RED_ALL,
    },
    {
        "name": "Current = 1 (0x01), all RED",
        "c1": [0x01, 0x01, 0x01, 0x01],
        "c2": [0xFE, 0xFE, 0xFE, 0xFE],
        "pixels": RED_ALL,
    },

    # =====================================================
    # SECTION 2: Per-channel current control
    # Set different current per WRGB channel in C1.
    # All pixel PWM = 255 to see the current effect.
    # =====================================================
    {
        "section": "\n  === SECTION 2: Per-channel current in C1 ===",
        "name": "W=63, R=0, G=0, B=0 — only W has high current",
        "c1": [0x3F, 0x00, 0x00, 0x00],
        "c2": [0xC0, 0xFF, 0xFF, 0xFF],
        "pixels": FULL_ALL,
    },
    {
        "name": "W=0, R=63, G=0, B=0 — only R has high current",
        "c1": [0x00, 0x3F, 0x00, 0x00],
        "c2": [0xFF, 0xC0, 0xFF, 0xFF],
        "pixels": FULL_ALL,
    },
    {
        "name": "W=0, R=0, G=63, B=0 — only G has high current",
        "c1": [0x00, 0x00, 0x3F, 0x00],
        "c2": [0xFF, 0xFF, 0xC0, 0xFF],
        "pixels": FULL_ALL,
    },
    {
        "name": "W=0, R=0, G=0, B=63 — only B has high current",
        "c1": [0x00, 0x00, 0x00, 0x3F],
        "c2": [0xFF, 0xFF, 0xFF, 0xC0],
        "pixels": FULL_ALL,
    },

    # =====================================================
    # SECTION 3: Broken C2 (C2 != ~C1)
    # Datasheet says chip will not decode data correctly.
    # This tests whether a bad C2 causes the chip to
    # reject the frame entirely.
    # =====================================================
    {
        "section": "\n  === SECTION 3: Invalid C2 (should fail to decode) ===",
        "name": "C1=0x1E, C2=0x1E (same as C1, NOT inverted)",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": RED_ALL,
    },
    {
        "name": "C1=0x1E, C2=0x00 (wrong inversion)",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0x00, 0x00, 0x00, 0x00],
        "pixels": RED_ALL,
    },
    {
        "name": "C1=0x1E, C2=0xFF (wrong inversion)",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": RED_ALL,
    },

    # =====================================================
    # SECTION 4: No C1/C2 — just pixel data
    # Tests what happens if C1/C2 are omitted entirely.
    # =====================================================
    {
        "section": "\n  === SECTION 4: No C1/C2 (data only) ===",
        "name": "Skip C1/C2, send pixel data directly",
        "c1": None,
        "c2": None,
        "pixels": RED_ALL,
    },

    # =====================================================
    # SECTION 5: C1/C2 with white channel test
    # Verify current setting affects white LED brightness.
    # =====================================================
    {
        "section": "\n  === SECTION 5: White LED current sweep ===",
        "name": "W current=63, R/G/B current=0, pixels=W only",
        "c1": [0x3F, 0x00, 0x00, 0x00],
        "c2": [0xC0, 0xFF, 0xFF, 0xFF],
        "pixels": WHITE_ALL,
    },
    {
        "name": "W current=10, R/G/B current=0, pixels=W only",
        "c1": [0x0A, 0x00, 0x00, 0x00],
        "c2": [0xF5, 0xFF, 0xFF, 0xFF],
        "pixels": WHITE_ALL,
    },
    {
        "name": "W current=0 (min 6.5mA), R/G/B current=0, pixels=W only",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": WHITE_ALL,
    },

    # =====================================================
    # SECTION 6: All off baseline
    # =====================================================
    {
        "section": "\n  === SECTION 6: All off ===",
        "name": "Current=0, PWM=0",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 62)
    print("  TM1815B C1/C2 Current Register Test")
    print(f"  {NUM_LEDS} LEDs @ {SPI_SPEED/1e6:.1f} MHz SPI")
    print()
    print("  C1 format: [W(6bit), R(6bit), G(6bit), B(6bit)]")
    print("  C2 must = bitwise NOT of C1")
    print("  Bits 7,6 of each C1 byte must be 0")
    print()
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 62)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        if c1 is not None:
            print(f"         C1: [{', '.join(f'0x{b:02X}' for b in c1)}]")
            print(f"         C2: [{', '.join(f'0x{b:02X}' for b in c2)}]")
            valid = all(c2[j] == (c1[j] ^ 0xFF) for j in range(4))
            print(f"         C2 == ~C1: {valid}")
        else:
            print("         C1/C2: OMITTED")
        print(f"         Pixels: W={pixels[0][0]} R={pixels[0][1]}"
              f" G={pixels[0][2]} B={pixels[0][3]} (x{len(pixels)})")

        input("         Press Enter to start...")

        if c1 is not None:
            buf = build_frame_raw(c1, c2, pixels)
        else:
            buf = bytearray(b'\xFF' * 80)
            for w, r, g, b in pixels:
                buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
            buf += b'\xFF' * 80

        frames = run_test(list(buf))
        print(f"         Sent {frames} frames")

    print("\n  Done.")
    print("  Key observations:")
    print("    1. Did current level affect LED brightness? (Section 1)")
    print("    2. Could you see per-channel current differences? (Section 2)")
    print("    3. Did invalid C2 cause the frame to be rejected? (Section 3)")
    print("    4. Did omitting C1/C2 still work? (Section 4)")
    print("    5. Did white LED brightness track W current? (Section 5)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
