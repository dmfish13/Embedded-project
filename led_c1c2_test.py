#!/usr/bin/env python3
"""
C1/C2 forwarding test — 16-bit encoding at 4.0 MHz.

Previous findings:
  - Pi 5 SPI only works cleanly at 2.0 and 4.0 MHz
  - 4-bit @ 2.0 MHz: PCB1 works, forwarding fails (0 LOW=500ns < 620ns)
  - 10-bit @ 4.0 MHz: fails because 10-bit groups don't align to SPI
    byte boundaries — inter-byte gaps corrupt LOW pulses mid-bit

16-bit encoding (2 SPI bytes per data bit):
  Logic 0: byte1=0x1F (00011111) + byte2=0xFF → 750ns LOW, 3250ns HIGH
  Logic 1: byte1=0x03 (00000011) + byte2=0xFF → 1500ns LOW, 2500ns HIGH
  Bit period = 16 × 250ns = 4000ns = 250 KHz (in 200-400 KHz spec)

  The LOW pulse is entirely within byte 1. Byte 2 is always 0xFF.
  Inter-byte gaps only occur at HIGH→HIGH boundaries — they can
  NEVER split or corrupt a LOW pulse.

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
    return max(math.ceil(RESET_TARGET_US * spi_speed / 8_000_000), 50)


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


# === 16-bit encoding (2 bytes per data bit, gap-safe) ===

def encode_byte_16bit(value):
    """Encode one data byte into 16 SPI bytes (2 per data bit).

    Each data bit becomes 2 SPI bytes:
      Logic 0: 0x1F + 0xFF → 00011111 11111111 → 3 LOW + 13 HIGH
      Logic 1: 0x03 + 0xFF → 00000011 11111111 → 6 LOW + 10 HIGH

    The entire LOW pulse fits in byte 1. Byte 2 is always 0xFF.
    Inter-byte gaps only occur at HIGH→HIGH transitions.
    """
    result = bytearray(16)
    for i in range(8):
        bit_pos = 7 - i
        idx = i * 2
        if value & (1 << bit_pos):
            result[idx] = 0x03
        else:
            result[idx] = 0x1F
        result[idx + 1] = 0xFF
    return bytes(result)


LUT_16BIT = [encode_byte_16bit(v) for v in range(256)]


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
    for i, px in enumerate(pixels, 1):
        print(f"         C1={fmt_c(c1)}  C2={fmt_c(c2)}  "
              f"D{i}: {fmt_d(px)}")


UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    # =================================================================
    # SECTION 1: 16-bit @ 4.0 MHz — gap-safe, in-spec timing
    # LOW pulse entirely in byte 1, byte 2 always 0xFF.
    # 0 LOW = 750ns (620-820), 1 LOW = 1500ns (1300-2000)
    # Period = 4µs = 250 KHz (200-400 KHz spec)
    # =================================================================
    {
        "section": "\n  === SECTION 1: 16-bit @ 4.0 MHz (gap-safe, in-spec) ===",
        "name": "16-bit 4.0 MHz — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "16-bit 4.0 MHz — All RED",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "16-bit 4.0 MHz — All GREEN",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },
    {
        "name": "16-bit 4.0 MHz — All BLUE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 255)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 2: Individual PCB addressing
    # =================================================================
    {
        "section": "\n  === SECTION 2: 16-bit 4.0 MHz — one PCB at a time ===",
        "name": "Only PCB1 = RED",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB2 = GREEN",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 255, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB3 = BLUE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 255), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB4 = WHITE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 3: C1 current values
    # =================================================================
    {
        "section": "\n  === SECTION 3: 16-bit 4.0 MHz — C1 current values ===",
        "name": "Current=0 (minimum 6.5mA)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": UNIQUE,
    },
    {
        "name": "Current=63 (0x3F, maximum 38mA)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x3F, 0x3F, 0x3F, 0x3F],
        "c2": [0xC0, 0xC0, 0xC0, 0xC0],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 4: 4-bit @ 2.0 MHz baseline (known: PCB1 only)
    # =================================================================
    {
        "section": "\n  === SECTION 4: 4-bit @ 2.0 MHz baseline ===",
        "name": "4-bit 2.0 MHz — unique colors (PCB1 should work)",
        "speed": 2_000_000,
        "encoding": "4bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 5: All off
    # =================================================================
    {
        "section": "\n  === SECTION 5: All off ===",
        "name": "16-bit 4.0 MHz — all off",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — 16-Bit Encoding @ 4.0 MHz")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Why 16-bit: 10-bit encoding failed because 10-bit groups")
    print("  don't align to SPI byte boundaries. Inter-byte gaps fell")
    print("  inside LOW pulses, corrupting the data.")
    print()
    print("  16-bit encoding (2 SPI bytes per data bit):")
    print("    Logic 0: 0x1F + 0xFF → 3 LOW + 13 HIGH = 750ns LOW")
    print("    Logic 1: 0x03 + 0xFF → 6 LOW + 10 HIGH = 1500ns LOW")
    print("    Period = 4µs = 250 KHz (in 200-400 KHz spec)")
    print()
    print("    LOW pulse is entirely in byte 1. Byte 2 = 0xFF (all HIGH).")
    print("    Inter-byte gaps ONLY occur at HIGH→HIGH transitions.")
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
        encoding = test.get("encoding", "16bit")
        spi_mode = test.get("spi_mode", 0)

        for j in range(4):
            if c2[j] != (c1[j] ^ 0xFF):
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT "
                      f"~C1[{j}]=0x{c1[j]:02X} "
                      f"(expected 0x{c1[j] ^ 0xFF:02X}) ***")
                sys.exit(1)

        if encoding == "16bit":
            lut = LUT_16BIT
            t0_ns = int(1e9 / speed * 3)
            t1_ns = int(1e9 / speed * 6)
            bits_per = 16
        else:
            lut = LUT_4BIT
            t0_ns = int(1e9 / speed * 1)
            t1_ns = int(1e9 / speed * 3)
            bits_per = 4

        reset = reset_bytes_for_speed(speed)
        reset_us = reset * 8 / speed * 1e6

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: {bits_per}-bit | SPI: {speed/1e6:.1f} MHz "
              f"Mode {spi_mode} | 0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        frame = build_frame(c1, c2, pixels, lut, reset)
        print(f"         Frame: {len(frame)} bytes | "
              f"Reset: {reset} bytes = {reset_us:.0f}µs | "
              f"C2 == ~C1: verified")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        frames = run_test(list(frame), speed, spi_mode)
        print(f"         Sent {frames} frames")

    print("\n  Done. Key questions:")
    print("    1. Did 16-bit @ 4.0 MHz make PCB1 respond? (Section 1)")
    print("    2. Did PCBs 2-4 respond? (FORWARDING!)")
    print("    3. Could each PCB be addressed individually? (Section 2)")
    print("    4. Did 4-bit baseline still work for PCB1? (Section 4)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
