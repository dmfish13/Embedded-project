#!/usr/bin/env python3
"""
PCB1 color cycle test — 20 colors (15 from button_map.py + 5 whites).

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, C1=[0x1E,0x1E,0x1E,0x1E], 4 pixels.

Colors from button_map.py are RGBW tuples (R,G,B,W).
TM1815B frame order is WRGB, so we reorder before sending.

Press Enter to advance — the next color starts immediately
with no inactive period between colors.

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
# 5 white temperatures — values given in WRGB order, stored as RGBW
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
    # 5 white temperatures (user values in WRGB, stored as RGBW)
    ("Candlelight ~1800K",    (255, 128, 0,   76)),
    ("Warm White ~3000K",     (255, 128, 12,  255)),
    ("Neutral White ~4000K",  (0,   0,   0,   255)),
    ("Cool White ~5000K",     (0,   64,  128, 217)),
    ("Daylight ~6500K",       (0,   128, 255, 178)),
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
    print("  PCB1 Color Cycle — 20 colors (15 button_map + 5 whites)")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (same as Section 5 baseline)")
    print("  C1=[0x1E, 0x1E, 0x1E, 0x1E]  C2=[0xE1, 0xE1, 0xE1, 0xE1]")
    print("  All 4 PCBs get the same color each test.")
    print()
    print("  Seamless transitions — next color starts immediately on Enter.")
    print("  Press Enter to START, then Enter to advance to next color.")
    print("=" * 68)

    c1 = [0x1E, 0x1E, 0x1E, 0x1E]
    c2 = [0xE1, 0xE1, 0xE1, 0xE1]
    speed = 2_000_000
    reset = 80

    for j in range(4):
        if c2[j] != (c1[j] ^ 0xFF):
            print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT "
                  f"~C1[{j}]=0x{c1[j]:02X} "
                  f"(expected 0x{c1[j] ^ 0xFF:02X}) ***")
            sys.exit(1)

    t0_ns = int(1e9 / speed * 1)
    t1_ns = int(1e9 / speed * 3)

    # Pre-build all frame buffers
    bufs = []
    for test in TESTS:
        buf = build_frame(c1, c2, test["pixels"], LUT_4BIT, reset_bytes=reset)
        bufs.append(list(buf))

    # Print all test info
    for i, test in enumerate(TESTS, 1):
        pixels = test["pixels"]
        frame_bytes = len(build_frame(c1, c2, pixels, LUT_4BIT, reset))
        print(f"\n  [{i:>2}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: 4-bit | SPI: {speed/1e6:.1f} MHz | "
              f"0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        print(f"         Frame: {frame_bytes} bytes | C2 == ~C1: verified")
        print_test_info(c1, c2, pixels)

    # Wait for user to start
    input("\n  Press Enter to start color 1...")

    # Open SPI once, keep it open for all colors
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = speed
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    print(f"\n  Requested {speed/1e6:.1f} MHz, actual {actual/1e6:.3f} MHz")
    sys.stdout.flush()

    current_idx = 0
    advance = threading.Event()
    stop = threading.Event()

    def wait_for_enter():
        while not stop.is_set():
            input()
            advance.set()

    t = threading.Thread(target=wait_for_enter, daemon=True)
    t.start()

    for i in range(len(TESTS)):
        current_idx = i
        advance.clear()
        name = TESTS[i]["name"]
        print(f"\n  [{i+1:>2}/{len(TESTS)}] NOW: {name}")
        if i < len(TESTS) - 1:
            print(f"         Press Enter for next color...")
        else:
            print(f"         Press Enter to finish...")
        sys.stdout.flush()

        frame_count = 0
        while not advance.is_set():
            spi.xfer2(bufs[i])
            frame_count += 1

        print(f"         Sent {frame_count} frames")

    stop.set()
    spi.close()
    print(f"\n  Done. Did PCB1 change color for each of the {len(TESTS)} tests?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
