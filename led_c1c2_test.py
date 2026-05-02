#!/usr/bin/env python3
"""
C1/C2 forwarding test — uses 8-bit encoding at 3.2 MHz to achieve
in-spec TM1815B timing with byte-aligned data bits.

Previous findings:
  - 4-bit encoding at 2.0 MHz: PCB1 works, PCBs 2-4 don't forward
    (logic 0 LOW = 500ns, below 620ns spec)
  - 4-bit encoding at 1.6 MHz: NOTHING works, even PCB1 freezes
    (Pi 5 SPI issue at non-2.0 MHz speeds)

New approach — 8-bit encoding (1 SPI byte per data bit):
  Logic 0: 0x3F = 00111111 → 2 LOW bits + 6 HIGH bits
  Logic 1: 0x07 = 00000111 → 5 LOW bits + 3 HIGH bits

  At 3.2 MHz SPI:
    Logic 0 LOW = 2 × 312.5ns = 625ns  (in 620-820ns spec)
    Logic 1 LOW = 5 × 312.5ns = 1562ns (in 1300-2000ns spec)
    Bit period  = 8 × 312.5ns = 2500ns (exactly 400 KHz)

  Each data bit = 1 SPI byte → inter-byte gaps fall between
  data bits (in HIGH region), not within them.

Setup: Pi → SN74AHCT125N → PCB1 → PCB2 → PCB3 → PCB4
       (10kΩ pull-up on GPIO 20 to 3.3V)

Usage:
    python3 led_c1c2_test.py
"""

import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4


# === 4-bit encoding (original, works at 2.0 MHz for PCB1 only) ===

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


# === 8-bit encoding (new, 1 SPI byte per data bit) ===

def encode_byte_8bit(value):
    result = bytearray(8)
    for i in range(8):
        bit_pos = 7 - i
        if value & (1 << bit_pos):
            result[i] = 0x07
        else:
            result[i] = 0x3F
    return bytes(result)


LUT_8BIT = [encode_byte_8bit(v) for v in range(256)]


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


UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    # =================================================================
    # SECTION 1: 8-bit encoding — the main test
    # 3.2 MHz SPI × 8 bits/data-bit = 400 KHz data rate
    # Logic 0 LOW = 625ns, Logic 1 LOW = 1562ns — both in spec
    # =================================================================
    {
        "section": "\n  === SECTION 1: 8-bit encoding @ 3.2 MHz (all timing in spec) ===",
        "name": "8-bit 3.2 MHz — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": UNIQUE,
    },
    {
        "name": "8-bit 3.2 MHz — All RED",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "8-bit 3.2 MHz — All GREEN",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 2: 8-bit encoding speed variations
    # Try nearby SPI speeds that also give in-spec timing
    # =================================================================
    {
        "section": "\n  === SECTION 2: 8-bit encoding at other speeds ===",
        "name": "8-bit 2.8 MHz — unique colors (0 LOW=714ns, 1 LOW=1786ns)",
        "speed": 2_800_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": UNIQUE,
    },
    {
        "name": "8-bit 2.4 MHz — unique colors (0 LOW=833ns, 1 LOW=2083ns)",
        "speed": 2_400_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": UNIQUE,
    },
    {
        "name": "8-bit 4.0 MHz — unique colors (0 LOW=500ns OUT OF SPEC)",
        "speed": 4_000_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 3: 8-bit encoding C1 current values @ 3.2 MHz
    # =================================================================
    {
        "section": "\n  === SECTION 3: 8-bit 3.2 MHz — C1 current values ===",
        "name": "Current=0 (minimum 6.5mA)",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "pixels": UNIQUE,
    },
    {
        "name": "Current=63 (0x3F, maximum 38mA)",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x3F, 0x3F, 0x3F, 0x3F],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 4: Individual PCB addressing @ 3.2 MHz 8-bit
    # =================================================================
    {
        "section": "\n  === SECTION 4: 8-bit 3.2 MHz — one PCB at a time ===",
        "name": "Only PCB1 = RED",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB2 = GREEN",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 0), (0, 0, 255, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB3 = BLUE",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 255), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB4 = WHITE",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 5: Baseline — 4-bit encoding at 2.0 MHz (known: PCB1 only)
    # =================================================================
    {
        "section": "\n  === SECTION 5: 4-bit encoding @ 2.0 MHz (baseline) ===",
        "name": "4-bit 2.0 MHz — unique colors (PCB1 should work)",
        "speed": 2_000_000,
        "encoding": "4bit",
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": UNIQUE,
    },

    # =================================================================
    # SECTION 6: All off
    # =================================================================
    {
        "section": "\n  === SECTION 6: All off ===",
        "name": "8-bit 3.2 MHz — all off",
        "speed": 3_200_000,
        "encoding": "8bit",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — 8-Bit Encoding")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Previous result: 4-bit encoding only works at 2.0 MHz (PCB1 only)")
    print()
    print("  NEW: 8-bit encoding (1 SPI byte = 1 data bit)")
    print("    Logic 0: 0x3F = 00111111 → short LOW + long HIGH")
    print("    Logic 1: 0x07 = 00000111 → long LOW + short HIGH")
    print("    At 3.2 MHz: 0 LOW=625ns  1 LOW=1562ns  period=2500ns")
    print("    All timing within TM1815B spec!")
    print()
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        c1 = test["c1"]
        c2 = [b ^ 0xFF for b in c1]
        pixels = test["pixels"]
        speed = test["speed"]
        encoding = test.get("encoding", "8bit")
        reset = test.get("reset", 80)

        lut = LUT_8BIT if encoding == "8bit" else LUT_4BIT
        bits_per = 8 if encoding == "8bit" else 4
        t0_ns = int(1e9 / speed * (2 if encoding == "8bit" else 1))
        t1_ns = int(1e9 / speed * (5 if encoding == "8bit" else 3))

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: {bits_per}-bit | SPI: {speed/1e6:.1f} MHz | "
              f"0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        frame_bytes = len(build_frame(c1, c2, pixels, lut, reset))
        print(f"         Frame: {frame_bytes} bytes")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        buf = build_frame(c1, c2, pixels, lut, reset_bytes=reset)
        frames = run_test(list(buf), speed)
        print(f"         Sent {frames} frames")

    print("\n  Done. Key questions:")
    print("    1. Did 8-bit @ 3.2 MHz make PCB1 respond? (Section 1)")
    print("    2. Did PCBs 2-4 respond? (FORWARDING!)")
    print("    3. Which 8-bit speeds worked? (Section 2)")
    print("    4. Could each PCB be addressed individually? (Section 4)")
    print("    5. Did the 4-bit baseline still work for PCB1? (Section 5)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
