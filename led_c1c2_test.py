#!/usr/bin/env python3
"""
C1/C2 forwarding test — 10-bit encoding at 4.0 MHz for in-spec timing,
with corrected reset duration and SPI mode testing.

Previous findings:
  - Pi 5 SPI only outputs clean waveforms at 2.0 MHz and 4.0 MHz
  - 4-bit @ 2.0 MHz: PCB1 works, forwarding fails (0 LOW=500ns < 620ns)
  - 8-bit @ 4.0 MHz: PCB1 works, forwarding fails (0 LOW=500ns < 620ns)
  - Earlier 10-bit @ 4.0 MHz: reset too short! 80 bytes × 8 bits × 250ns
    = 160µs, below the 200µs minimum. Chips never reset properly.

Fixes applied:
  1. Reset duration: calculated per SPI speed to guarantee >= 250µs
     At 4.0 MHz: 125 bytes of 0xFF = 250µs (>= 200µs min)
     At 2.0 MHz: 63 bytes of 0xFF = 252µs (was already OK at 80 bytes)
  2. 10-bit encoding for in-spec data timing at 4.0 MHz:
     Logic 0: 3 LOW + 7 HIGH = 750ns LOW (in 620-820ns)
     Logic 1: 6 LOW + 4 HIGH = 1500ns LOW (in 1300-2000ns)
     Bit period = 2500ns = exactly 400 KHz
  3. SPI Mode 3 test: CPOL=1, CPHA=1 may keep MOSI HIGH when idle,
     preventing spurious LOW pulses between xfer2 calls.

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
    """Calculate minimum 0xFF bytes needed for >= RESET_TARGET_US reset."""
    bytes_needed = math.ceil(RESET_TARGET_US * spi_speed / 8_000_000)
    return max(bytes_needed, 50)


# === 4-bit encoding (baseline, works at 2.0 MHz for PCB1 only) ===

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


# === 10-bit encoding (3 LOW for 0, 6 LOW for 1, designed for 4.0 MHz) ===

def encode_byte_10bit(value):
    """Encode one data byte (8 bits) into 80 SPI bits (10 bytes).

    Each data bit becomes 10 SPI bits (MSB first on wire):
      Logic 0: 0001111111 → 3 LOW + 7 HIGH
      Logic 1: 0000001111 → 6 LOW + 4 HIGH

    At 4.0 MHz: 0 LOW = 750ns, 1 LOW = 1500ns, period = 2500ns.
    """
    bits = 0
    for bit_pos in range(7, -1, -1):
        bits <<= 10
        if value & (1 << bit_pos):
            bits |= 0b0000001111
        else:
            bits |= 0b0001111111
    result = bytearray(10)
    for i in range(10):
        result[9 - i] = bits & 0xFF
        bits >>= 8
    return bytes(result)


LUT_10BIT = [encode_byte_10bit(v) for v in range(256)]


def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes):
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


def run_test(buf_list, spi_speed, spi_mode=0):
    """Send continuously at given speed/mode until Enter is pressed."""
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
    """Print C1, C2, and each D(n) on separate lines."""
    for i, px in enumerate(pixels, 1):
        print(f"         C1={fmt_c(c1)}  C2={fmt_c(c2)}  "
              f"D{i}: {fmt_d(px)}")


UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    # =================================================================
    # SECTION 1: 10-bit @ 4.0 MHz, SPI Mode 0 — corrected reset
    # Previous attempt failed because reset was only 160µs (< 200µs).
    # Now using 125 bytes = 250µs.
    # =================================================================
    {
        "section": "\n  === SECTION 1: 10-bit @ 4.0 MHz, Mode 0, reset=250µs ===",
        "name": "10-bit 4.0 MHz — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "10-bit 4.0 MHz — All RED",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "10-bit 4.0 MHz — All GREEN",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },
    {
        "name": "10-bit 4.0 MHz — All BLUE",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 255)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 2: 10-bit @ 4.0 MHz, SPI Mode 3 — idle HIGH
    # Mode 3 (CPOL=1, CPHA=1) may keep MOSI HIGH between transfers,
    # preventing spurious LOW glitches that confuse the TM1815B.
    # =================================================================
    {
        "section": "\n  === SECTION 2: 10-bit @ 4.0 MHz, Mode 3 (idle HIGH) ===",
        "name": "Mode 3 — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 3,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "Mode 3 — All RED",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 3,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 3: Individual PCB addressing @ 4.0 MHz 10-bit
    # Uses whichever SPI mode works from Sections 1-2.
    # =================================================================
    {
        "section": "\n  === SECTION 3: 10-bit 4.0 MHz — one PCB at a time (Mode 0) ===",
        "name": "Only PCB1 = RED",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB2 = GREEN",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 255, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB3 = BLUE",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 255), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB4 = WHITE",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 4: C1 current values @ 4.0 MHz 10-bit
    # =================================================================
    {
        "section": "\n  === SECTION 4: 10-bit 4.0 MHz — C1 current values ===",
        "name": "Current=0 (minimum 6.5mA)",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": UNIQUE,
    },
    {
        "name": "Current=63 (0x3F, maximum 38mA)",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x3F, 0x3F, 0x3F, 0x3F],
        "c2": [0xC0, 0xC0, 0xC0, 0xC0],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 5: 4-bit @ 2.0 MHz baseline (known: PCB1 only)
    # Reset = 80 bytes = 320µs at 2.0 MHz (OK)
    # =================================================================
    {
        "section": "\n  === SECTION 5: 4-bit @ 2.0 MHz baseline ===",
        "name": "4-bit 2.0 MHz Mode 0 — unique colors (PCB1 should work)",
        "speed": 2_000_000,
        "encoding": "4bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "4-bit 2.0 MHz Mode 3 — unique colors",
        "speed": 2_000_000,
        "encoding": "4bit",
        "spi_mode": 3,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 6: All off
    # =================================================================
    {
        "section": "\n  === SECTION 6: All off ===",
        "name": "10-bit 4.0 MHz — all off",
        "speed": 4_000_000,
        "encoding": "10bit",
        "spi_mode": 0,
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — Fixed Reset + 10-Bit Encoding")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Fixes from previous run:")
    print("    1. Reset duration: now >= 250µs (was 160µs at 4 MHz!)")
    print("    2. 10-bit encoding: 0 LOW=750ns, 1 LOW=1500ns (in spec)")
    print("    3. SPI Mode 3 test: may keep MOSI HIGH when idle")
    print()
    print("  10-bit encoding at 4.0 MHz:")
    print("    Logic 0: 0001111111 → 3 LOW + 7 HIGH = 750ns LOW")
    print("    Logic 1: 0000001111 → 6 LOW + 4 HIGH = 1500ns LOW")
    print("    Bit period = 2500ns = 400 KHz")
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
        encoding = test.get("encoding", "10bit")
        spi_mode = test.get("spi_mode", 0)

        for j in range(4):
            if c2[j] != (c1[j] ^ 0xFF):
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT "
                      f"~C1[{j}]=0x{c1[j]:02X} "
                      f"(expected 0x{c1[j] ^ 0xFF:02X}) ***")
                sys.exit(1)

        lut = LUT_10BIT if encoding == "10bit" else LUT_4BIT
        reset = reset_bytes_for_speed(speed)
        reset_us = reset * 8 / speed * 1e6

        if encoding == "10bit":
            t0_ns = int(1e9 / speed * 3)
            t1_ns = int(1e9 / speed * 6)
            bits_per = 10
        else:
            t0_ns = int(1e9 / speed * 1)
            t1_ns = int(1e9 / speed * 3)
            bits_per = 4

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: {bits_per}-bit | SPI: {speed/1e6:.1f} MHz "
              f"Mode {spi_mode} | 0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        frame = build_frame(c1, c2, pixels, lut, reset)
        print(f"         Frame: {len(frame)} bytes | "
              f"Reset: {reset} bytes = {reset_us:.0f}µs (>= 200µs) | "
              f"C2 == ~C1: verified")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        frames = run_test(list(frame), speed, spi_mode)
        print(f"         Sent {frames} frames")

    print("\n  Done. Key questions:")
    print("    1. Did 10-bit @ 4.0 MHz Mode 0 work now? (Section 1)")
    print("    2. Did Mode 3 make a difference? (Section 2)")
    print("    3. Did PCBs 2-4 respond? (FORWARDING!)")
    print("    4. Could each PCB be addressed individually? (Section 3)")
    print("    5. Did 4-bit baseline still work for PCB1? (Section 5)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
