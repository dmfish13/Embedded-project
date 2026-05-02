#!/usr/bin/env python3
"""
PCB1 color cycle test — 16 colors from button_map.py.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, C1=[0x1E,0x1E,0x1E,0x1E], 4 pixels.

Colors from button_map.py are RGBW tuples (R,G,B,W).
TM1815B frame order is WRGB, so we reorder before sending.

Usage:
    python3 led_color_cycle_test.py
"""

import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4


def encode_byte_4bit(value):
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


LUT_4BIT = [encode_byte_4bit(v) for v in range(256)]


def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes=80):
    """Build a TM1815B frame: [Reset][C1][C2][D1..Dn][Reset]"""
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf


def run_test(buf_list, spi_speed):
    """Send continuously at given speed until Enter is pressed."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    frame_count = 0
    running = True

    print(f"         Requested {spi_speed/1e6:.1f} MHz, "
          f"actual {actual/1e6:.3f} MHz")
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


def fmt_c(c_bytes):
    return f"[0x{c_bytes[0]:02X}, 0x{c_bytes[1]:02X}, 0x{c_bytes[2]:02X}, 0x{c_bytes[3]:02X}]"


def fmt_d(pixel):
    return f"W={pixel[0]:>3} R={pixel[1]:>3} G={pixel[2]:>3} B={pixel[3]:>3}"


def print_test_info(c1, c2, pixels):
    """Print C1, C2, and each D(n) on separate lines."""
    for i, px in enumerate(pixels, 1):
        print(f"         C1={fmt_c(c1)}  C2={fmt_c(c2)}  "
              f"D{i}: {fmt_d(px)}")


def rgbw_to_wrgb(r, g, b, w):
    return (w, r, g, b)


# 16 colors from button_map.py — (name, RGBW tuple)
COLORS = [
    ("Deep Red",    (180, 0,   0,   0)),
    ("Mint",        (0,   200, 120, 0)),
    ("Dark Blue",   (0,   0,   139, 0)),
    ("Red Prime",   (255, 0,   0,   0)),
    ("Orange",      (255, 100, 0,   0)),
    ("Light Blue",  (100, 150, 255, 0)),
    ("Violet",      (148, 0,   211, 0)),
    ("Green Prime", (0,   255, 0,   0)),
    ("Yellow",      (255, 255, 0,   0)),
    ("Cyan",        (0,   255, 255, 0)),
    ("Purple",      (128, 0,   128, 0)),
    ("Blue Prime",  (0,   0,   255, 0)),
    ("Neon Yellow", (220, 255, 0,   0)),
    ("Steel Blue",  (70,  130, 180, 0)),
    ("Magenta",     (255, 0,   255, 0)),
    ("White",       (0,   0,   0,   255)),
]

# Build TESTS list — same format as led_c1c2_test.py Section 5 baseline
TESTS = []
for name, rgbw in COLORS:
    r, g, b, w = rgbw
    wrgb = rgbw_to_wrgb(r, g, b, w)
    TESTS.append({
        "name": f"4-bit 2.0 MHz — {name} (R={r} G={g} B={b} W={w})",
        "speed": 2_000_000,
        "encoding": "4bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [wrgb] * NUM_LEDS,
    })


def main():
    print("=" * 68)
    print("  PCB1 Color Cycle — 16 colors from button_map.py")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (same as Section 5 baseline)")
    print("  C1=[0x1E, 0x1E, 0x1E, 0x1E]  C2=[0xE1, 0xE1, 0xE1, 0xE1]")
    print("  All 4 PCBs get the same color each test.")
    print()
    print("  Does PCB1 change color for each test?")
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]
        speed = test["speed"]
        reset = test.get("reset", 80)

        for j in range(4):
            if c2[j] != (c1[j] ^ 0xFF):
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT "
                      f"~C1[{j}]=0x{c1[j]:02X} "
                      f"(expected 0x{c1[j] ^ 0xFF:02X}) ***")
                sys.exit(1)

        t0_ns = int(1e9 / speed * 1)
        t1_ns = int(1e9 / speed * 3)

        print(f"\n  [{i:>2}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: 4-bit | SPI: {speed/1e6:.1f} MHz | "
              f"0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        frame_bytes = len(build_frame(c1, c2, pixels, LUT_4BIT, reset))
        print(f"         Frame: {frame_bytes} bytes | "
              f"C2 == ~C1: verified")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        buf = build_frame(c1, c2, pixels, LUT_4BIT, reset_bytes=reset)
        frames = run_test(list(buf), speed)
        print(f"         Sent {frames} frames")

    print("\n  Done. Did PCB1 change color for each of the 16 tests?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
