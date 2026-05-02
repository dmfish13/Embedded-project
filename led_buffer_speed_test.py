#!/usr/bin/env python3
"""
Buffer forwarding speed sweep — tests different SPI speeds and preamble
lengths to find what makes the buffer TM1815B properly forward data
to downstream LED PCBs.

Setup: Pi → level shifter → buffer TM1815B (no LEDs) → PCB1 → PCB2...

The pull-up resistor on GPIO 20 keeps MOSI HIGH during inter-byte gaps,
so lower SPI speeds (1.6 MHz) may now work for forwarding.

Usage:
    python3 led_buffer_speed_test.py
"""

import sys
import threading
from spidev import SpiDev


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


def build_frame(pixels, current=30, preamble=80, trailing=80):
    """Build frame with configurable preamble/trailing length."""
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([c1_val ^ 0xFF] * 4)

    buf = bytearray(b'\xFF' * preamble)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for w, r, g, b in pixels:
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
    buf += b'\xFF' * trailing
    return buf


def run_test(spi_speed, buf_list):
    """Send continuously at given speed until Enter is pressed."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
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


OFF = (0, 0, 0, 0)
RED = (0, 255, 0, 0)
GREEN = (0, 0, 255, 0)
BLUE = (0, 0, 0, 255)

# Frame payload: 1 dummy (buffer) + 4 LED PCBs, each a different color
PIXELS_UNIQUE = [OFF, RED, GREEN, BLUE, RED]
# Simple: all red after dummy
PIXELS_ALL_RED = [OFF, RED, RED, RED, RED]

TESTS = [
    # =====================================================
    # SECTION 1: SPI speed sweep with standard preamble
    # The buffer's forwarding logic may require the native
    # 400 kHz data rate (= 1.6 MHz SPI with 4-bit encoding)
    # =====================================================
    {
        "section": "\n  === SECTION 1: Speed sweep (preamble=80, 1 dummy + 4 LEDs) ===",
        "name": "1.6 MHz (exact 400 kHz data rate)",
        "speed": 1_600_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 80,
        "trailing": 80,
    },
    {
        "name": "1.8 MHz (450 kHz data rate)",
        "speed": 1_800_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 80,
        "trailing": 80,
    },
    {
        "name": "2.0 MHz (500 kHz data rate — worked for PCB1 direct)",
        "speed": 2_000_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 80,
        "trailing": 80,
    },
    {
        "name": "2.4 MHz (600 kHz data rate)",
        "speed": 2_400_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 80,
        "trailing": 80,
    },
    {
        "name": "3.2 MHz (800 kHz data rate)",
        "speed": 3_200_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 80,
        "trailing": 80,
    },

    # =====================================================
    # SECTION 2: Preamble length sweep at 2.0 MHz
    # Maybe the buffer needs a longer reset before it
    # starts forwarding, or a shorter one to avoid re-entering
    # demo mode on downstream chips.
    # =====================================================
    {
        "section": "\n  === SECTION 2: Preamble sweep @ 2.0 MHz ===",
        "name": "Preamble = 20 bytes (80 µs reset)",
        "speed": 2_000_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 20,
        "trailing": 20,
    },
    {
        "name": "Preamble = 40 bytes (160 µs reset)",
        "speed": 2_000_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 40,
        "trailing": 40,
    },
    {
        "name": "Preamble = 200 bytes (800 µs reset)",
        "speed": 2_000_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 200,
        "trailing": 200,
    },
    {
        "name": "Preamble = 500 bytes (2 ms reset)",
        "speed": 2_000_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 500,
        "trailing": 500,
    },

    # =====================================================
    # SECTION 3: Preamble sweep at 1.6 MHz
    # =====================================================
    {
        "section": "\n  === SECTION 3: Preamble sweep @ 1.6 MHz ===",
        "name": "1.6 MHz, preamble = 20 bytes (100 µs)",
        "speed": 1_600_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 20,
        "trailing": 20,
    },
    {
        "name": "1.6 MHz, preamble = 200 bytes (1 ms)",
        "speed": 1_600_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 200,
        "trailing": 200,
    },
    {
        "name": "1.6 MHz, preamble = 500 bytes (2.5 ms)",
        "speed": 1_600_000,
        "pixels": PIXELS_ALL_RED,
        "preamble": 500,
        "trailing": 500,
    },

    # =====================================================
    # SECTION 4: Unique colors per PCB (verify addressing)
    # If forwarding works at some speed, this confirms
    # each PCB gets its own data.
    # =====================================================
    {
        "section": "\n  === SECTION 4: Unique colors (BUF=off, PCB1=R, PCB2=G, PCB3=B, PCB4=R) ===",
        "name": "1.6 MHz, unique colors",
        "speed": 1_600_000,
        "pixels": PIXELS_UNIQUE,
        "preamble": 80,
        "trailing": 80,
    },
    {
        "name": "2.0 MHz, unique colors",
        "speed": 2_000_000,
        "pixels": PIXELS_UNIQUE,
        "preamble": 80,
        "trailing": 80,
    },

    # =====================================================
    # SECTION 5: No dummy (D0 goes to PCB1 directly)
    # Control test: if buffer eats D0, PCB1 should show
    # wrong color or go dark.
    # =====================================================
    {
        "section": "\n  === SECTION 5: No dummy — control test ===",
        "name": "2.0 MHz, no dummy: [RED, GREEN, BLUE, RED]",
        "speed": 2_000_000,
        "pixels": [RED, GREEN, BLUE, RED],
        "preamble": 80,
        "trailing": 80,
    },
]


def main():
    print("=" * 62)
    print("  Buffer TM1815B Speed & Preamble Sweep")
    print("  Setup: Pi → shifter → buffer TM1815B → PCB1 → PCB2...")
    print()
    print("  Looking for the speed/preamble combo that enables")
    print("  forwarding so PCBs 2-4 show commanded colors.")
    print()
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 62)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        pixels = test["pixels"]
        speed = test["speed"]
        preamble = test["preamble"]
        trailing = test["trailing"]
        data_rate = speed / 4 / 1000

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Speed: {speed/1e6:.1f} MHz "
              f"({data_rate:.0f} kHz data rate)")
        print(f"         Preamble: {preamble} bytes "
              f"({preamble * 8 / speed * 1e6:.0f} µs)")
        print(f"         Pixels: {len(pixels)} "
              f"(1 buffer + {len(pixels)-1} LEDs)")

        input("         Press Enter to start...")

        buf = build_frame(pixels, preamble=preamble, trailing=trailing)
        frames = run_test(speed, list(buf))
        print(f"         Sent {frames} frames")

    print("\n  Done.")
    print("  Key question: at which speed/preamble did PCBs 2-4")
    print("  show RED (or their unique colors in Section 4)?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
