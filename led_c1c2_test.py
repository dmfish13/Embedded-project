#!/usr/bin/env python3
"""
C1/C2 forwarding test — per-chip C1/C2 frame format.

Previous findings (all encodings tested):
  - 4-bit @ 2.0 MHz: PCB1 works, forwarding fails
  - 10-bit @ 4.0 MHz: PCB1 works, forwarding fails
  - 16-bit @ 4.0 MHz: PCB1 works, forwarding fails
  - Symptom: PCB1 shows correct color, PCBs 2-4 cycle (demo mode)

Hypothesis: TM1815B uses WS2812-style cascading where each chip
consumes 12 bytes from the data stream (C1+C2+D) and forwards the
rest. Our original format sends C1/C2 only once:

  BROKEN:   [Reset] [C1][C2] [D1][D2][D3][D4] [Reset]
    Chip1 eats C1+C2+D1 (correct) → forwards D2,D3,D4
    Chip2 reads D2 as "C1" (WRONG — pixel data, not current config)

  PER-CHIP: [Reset] [C1][C2][D1] [C1][C2][D2] [C1][C2][D3]... [Reset]
    Chip1 eats C1+C2+D1 → forwards C1+C2+D2, C1+C2+D3...
    Chip2 reads C1+C2+D2 (CORRECT)

Setup: Pi → SN74AHCT125N → PCB1 → PCB2 → PCB3 → PCB4
       (10kΩ pull-up on GPIO 20 to 3.3V)

Usage:
    python3 led_c1c2_test.py
"""

import math
import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4
RESET_TARGET_US = 250


def reset_bytes_for_speed(spi_speed):
    return max(math.ceil(RESET_TARGET_US * spi_speed / 8_000_000), 50)


# === 4-bit encoding (known working for PCB1 at 2.0 MHz) ===

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


# === 8-bit encoding (1 SPI byte per data bit, in-spec at 4.0 MHz) ===

def encode_byte_8bit(value):
    """Encode one data byte into 8 SPI bytes (1 per data bit).

    Logic 0: 0x1F (00011111) → 3 LOW + 5 HIGH = 750ns LOW @ 4 MHz
    Logic 1: 0x03 (00000011) → 6 LOW + 2 HIGH = 1500ns LOW @ 4 MHz
    Period = 2000ns = 500 KHz (same as 4-bit @ 2.0 MHz)
    """
    result = bytearray(8)
    for i in range(8):
        bit_pos = 7 - i
        if value & (1 << bit_pos):
            result[i] = 0x03
        else:
            result[i] = 0x1F
    return bytes(result)


LUT_8BIT = [encode_byte_8bit(v) for v in range(256)]


# === Frame builders ===

def build_frame_global(c1_bytes, c2_bytes, pixels, lut, reset_bytes):
    """Original: [Reset][C1][C2][D1..Dn][Reset]"""
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf


def build_frame_perchip(c1_bytes, c2_bytes, pixels, lut, reset_bytes):
    """Per-chip: [Reset] [C1][C2][D1] [C1][C2][D2] ... [Reset]"""
    buf = bytearray(b'\xFF' * reset_bytes)
    for w, r, g, b in pixels:
        for bv in c1_bytes:
            buf += lut[bv]
        for bv in c2_bytes:
            buf += lut[bv]
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf


def run_test(buf_list, spi_speed, spi_mode=0):
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
    actual = spi.max_speed_hz
    spi.mode = spi_mode
    spi.lsbfirst = False

    frame_count = 0
    running = True

    print(f"         Requested {spi_speed/1e6:.1f} MHz, "
          f"actual {actual/1e6:.3f} MHz, "
          f"SPI mode {spi_mode}")
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
    for i, px in enumerate(pixels, 1):
        print(f"         C1={fmt_c(c1)}  C2={fmt_c(c2)}  "
              f"D{i}: {fmt_d(px)}")


UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    # =================================================================
    # SECTION 1: Per-chip C1/C2 format, 4-bit @ 2.0 MHz
    # Frame: [Reset][C1][C2][D1][C1][C2][D2][C1][C2][D3][C1][C2][D4][Reset]
    # Each chip gets its own C1/C2 header before its pixel data.
    # =================================================================
    {
        "section": "\n  === SECTION 1: Per-chip C1/C2, 4-bit @ 2.0 MHz ===",
        "name": "Per-chip — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "Per-chip — All RED",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "Per-chip — All GREEN",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },
    {
        "name": "Per-chip — All BLUE",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 255)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 2: Per-chip C1/C2 format, 8-bit @ 4.0 MHz
    # Same per-chip format but at higher speed with in-spec timing.
    # 0 LOW=750ns (620-820), 1 LOW=1500ns (1300-2000)
    # =================================================================
    {
        "section": "\n  === SECTION 2: Per-chip C1/C2, 8-bit @ 4.0 MHz ===",
        "name": "Per-chip 8bit — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 4_000_000,
        "encoding": "8bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "Per-chip 8bit — All RED",
        "speed": 4_000_000,
        "encoding": "8bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 3: Individual PCB addressing (per-chip format)
    # =================================================================
    {
        "section": "\n  === SECTION 3: Per-chip — one PCB at a time ===",
        "name": "Only PCB1 = RED",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB2 = GREEN",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 255, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB3 = BLUE",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 255), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB4 = WHITE",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 4: Original global C1/C2 (baseline — PCB1 works, rest fail)
    # =================================================================
    {
        "section": "\n  === SECTION 4: Original global C1/C2 (baseline) ===",
        "name": "Global C1/C2 — unique colors (PCB1 only, as before)",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "global",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 5: All off (per-chip format)
    # =================================================================
    {
        "section": "\n  === SECTION 5: All off ===",
        "name": "Per-chip — all off",
        "speed": 2_000_000,
        "encoding": "4bit",
        "frame_format": "perchip",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — Per-Chip C1/C2 Frame Format")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Previous: all encodings work for PCB1, forwarding always")
    print("  fails. The issue is likely FRAME FORMAT, not encoding.")
    print()
    print("  Hypothesis: each chip consumes 12 bytes (C1+C2+D) and")
    print("  forwards the rest. We must include C1/C2 before EACH")
    print("  chip's pixel data, not just once at the start.")
    print()
    print("  Per-chip: [Reset][C1][C2][D1][C1][C2][D2]...[Reset]")
    print("  Global:   [Reset][C1][C2][D1][D2][D3][D4][Reset]  ← broken")
    print()
    print("  C2 = bitwise NOT of C1 (validated before each test)")
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]
        speed = test["speed"]
        encoding = test.get("encoding", "4bit")
        frame_fmt = test.get("frame_format", "perchip")
        spi_mode = test.get("spi_mode", 0)

        for j in range(4):
            if c2[j] != (c1[j] ^ 0xFF):
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT "
                      f"~C1[{j}]=0x{c1[j]:02X} "
                      f"(expected 0x{c1[j] ^ 0xFF:02X}) ***")
                sys.exit(1)

        if encoding == "8bit":
            lut = LUT_8BIT
            t0_ns = int(1e9 / speed * 3)
            t1_ns = int(1e9 / speed * 6)
            bits_per = 8
        else:
            lut = LUT_4BIT
            t0_ns = int(1e9 / speed * 1)
            t1_ns = int(1e9 / speed * 3)
            bits_per = 4

        reset = reset_bytes_for_speed(speed)
        reset_us = reset * 8 / speed * 1e6

        builder = build_frame_perchip if frame_fmt == "perchip" else build_frame_global

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Format: {frame_fmt.upper()} | Encoding: {bits_per}-bit | "
              f"SPI: {speed/1e6:.1f} MHz Mode {spi_mode}")
        print(f"         0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        frame = builder(c1, c2, pixels, lut, reset)
        print(f"         Frame: {len(frame)} bytes | "
              f"Reset: {reset} bytes = {reset_us:.0f}µs | "
              f"C2 == ~C1: verified")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        frames = run_test(list(frame), speed, spi_mode)
        print(f"         Sent {frames} frames")

    print("\n  Done. Key questions:")
    print("    1. Did per-chip C1/C2 fix forwarding? (Sections 1-2)")
    print("       → PCBs 2-4 should show correct colors, NOT cycle!")
    print("    2. Did 8-bit @ 4.0 MHz also work? (Section 2)")
    print("    3. Could each PCB be addressed individually? (Section 3)")
    print("    4. Did global baseline still work for PCB1 only? (Section 4)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
