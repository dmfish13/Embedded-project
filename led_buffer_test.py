#!/usr/bin/env python3
"""
Buffer TM1815B forwarding test — sends data through the original
controller board's buffer TM1815B (no LEDs connected) to reach the
downstream LED PCB chain.

The buffer TM1815B consumes D1 (first pixel data), so the frame
must include an extra dummy pixel before the real LED data.

Tests different numbers of dummy pixels (1, 2, 0) to confirm the
buffer chip's behavior, and verifies that downstream LEDs receive
individually addressable colors.

Usage:
    python3 led_buffer_test.py
"""

import sys
import threading
from spidev import SpiDev

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


def build_frame(pixels, current=30):
    """Build frame from a list of (W, R, G, B) tuples.

    Each tuple becomes one D packet in the frame. The first tuple
    is consumed by the buffer TM1815B, the rest go to LED PCBs.
    """
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([c1_val ^ 0xFF] * 4)

    buf = bytearray(b'\xFF' * 80)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
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


# D0 = buffer TM1815B (dummy, no LEDs)
# D1 = LED PCB1, D2 = PCB2, D3 = PCB3, D4 = PCB4
OFF = (0, 0, 0, 0)
RED = (0, 255, 0, 0)
GREEN = (0, 0, 255, 0)
BLUE = (0, 0, 0, 255)
WHITE_W = (255, 0, 0, 0)
FULL = (255, 255, 255, 255)

TESTS = [
    # =====================================================
    # SECTION 1: Basic forwarding — 1 dummy + 4 LED pixels
    # Buffer eats D0, PCBs 1-4 get D1-D4
    # =====================================================
    {
        "section": "\n  === SECTION 1: 1 buffer dummy + 4 LED PCBs ===",
        "name": "All RED (D0=dummy, D1-D4=red)",
        "pixels": [OFF, RED, RED, RED, RED],
    },
    {
        "name": "All GREEN",
        "pixels": [OFF, GREEN, GREEN, GREEN, GREEN],
    },
    {
        "name": "All BLUE",
        "pixels": [OFF, BLUE, BLUE, BLUE, BLUE],
    },
    {
        "name": "All WHITE (W channel)",
        "pixels": [OFF, WHITE_W, WHITE_W, WHITE_W, WHITE_W],
    },
    {
        "name": "Each PCB a different color (R, G, B, W)",
        "pixels": [OFF, RED, GREEN, BLUE, WHITE_W],
    },
    {
        "name": "All OFF",
        "pixels": [OFF, OFF, OFF, OFF, OFF],
    },

    # =====================================================
    # SECTION 2: No dummy — direct to 4 LEDs (control test)
    # Same as before the buffer. Only PCB1 should respond.
    # If PCBs 2-4 also respond, the buffer isn't consuming D1.
    # =====================================================
    {
        "section": "\n  === SECTION 2: No dummy — 4 pixels direct (control) ===",
        "name": "No dummy: 4x RED (should only light PCB1 if buffer eats D1)",
        "pixels": [RED, RED, RED, RED],
    },

    # =====================================================
    # SECTION 3: 2 dummies — in case the buffer eats 2 slots
    # (C1+C2 might count as data to the buffer, or forwarding
    # has a 2-slot delay)
    # =====================================================
    {
        "section": "\n  === SECTION 3: 2 buffer dummies + 4 LED PCBs ===",
        "name": "2 dummies: D0-D1=off, D2-D5=RED",
        "pixels": [OFF, OFF, RED, RED, RED, RED],
    },
    {
        "name": "2 dummies: each PCB different (R, G, B, W)",
        "pixels": [OFF, OFF, RED, GREEN, BLUE, WHITE_W],
    },

    # =====================================================
    # SECTION 4: Individual LED addressing with 1 dummy
    # Only one PCB lit at a time — confirms forwarding works
    # for each position in the chain
    # =====================================================
    {
        "section": "\n  === SECTION 4: One PCB at a time (1 dummy) ===",
        "name": "Only PCB1 = RED",
        "pixels": [OFF, RED, OFF, OFF, OFF],
    },
    {
        "name": "Only PCB2 = GREEN",
        "pixels": [OFF, OFF, GREEN, OFF, OFF],
    },
    {
        "name": "Only PCB3 = BLUE",
        "pixels": [OFF, OFF, OFF, BLUE, OFF],
    },
    {
        "name": "Only PCB4 = WHITE",
        "pixels": [OFF, OFF, OFF, OFF, WHITE_W],
    },

    # =====================================================
    # SECTION 5: Full brightness all channels
    # =====================================================
    {
        "section": "\n  === SECTION 5: Full power ===",
        "name": "All PCBs full WRGB (1 dummy)",
        "pixels": [OFF, FULL, FULL, FULL, FULL],
    },
]


def main():
    print("=" * 62)
    print("  Buffer TM1815B Forwarding Test")
    print(f"  SPI @ {SPI_SPEED/1e6:.1f} MHz, byte order: W R G B")
    print()
    print("  The original controller has a buffer TM1815B (no LEDs)")
    print("  between the MCU and the LED chain. It consumes D0.")
    print("  D1-D4 go to LED PCBs 1-4.")
    print()
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 62)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        pixels = test["pixels"]
        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         {len(pixels)} pixel(s) in frame:")
        labels = ["BUF"] + [f"PCB{j}" for j in range(1, len(pixels))]
        for j, (w, r, g, b) in enumerate(pixels):
            tag = labels[j] if j < len(labels) else f"D{j}"
            print(f"           {tag}: W={w:>3} R={r:>3} G={g:>3} B={b:>3}")

        input("         Press Enter to start...")

        buf = build_frame(pixels)
        frames = run_test(list(buf))
        print(f"         Sent {frames} frames")

    print("\n  Done.")
    print("  Key results:")
    print("    Section 1: Did PCBs 2-4 show the commanded colors?")
    print("    Section 2: Did removing the dummy break forwarding?")
    print("    Section 3: Did 2 dummies shift the colors by one PCB?")
    print("    Section 4: Could you address each PCB individually?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
