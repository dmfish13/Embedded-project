#!/usr/bin/env python3
"""
TM1815B forwarding test — verified against datasheet + TM1814 Adafruit guide.

Confirmed correct per TM1815B_Datasheet_English.txt:
  - Timing: T0L=620-820ns, T1L=1300-2000ns, period=2.5-5us (200-400 KHz)
  - Frame: [Reset][C1(4)][C2(4)][D1(4)][D2(4)]...[Dn(4)][Reset]
    C1/C2 is GLOBAL (sent once). Chip auto-forwards with 32-bit delay.
  - Pixel order: W, R, G, B (each 8 bits, MSB first)
  - Reset: HIGH >= 200us
  - C1 byte: bits[7:6]=00, bits[5:0]=current (0=6.5mA, 63=38mA)
  - C2 = bitwise NOT of C1

16-bit encoding at 4.0 MHz (250ns/SPI bit):
  Logic 0: 0x1F+0xFF -> 3 LOW + 13 HIGH = 750ns LOW (spec 620-820) OK
  Logic 1: 0x03+0xFF -> 6 LOW + 10 HIGH = 1500ns LOW (spec 1300-2000) OK
  Period: 16 x 250ns = 4us = 250 KHz (spec 200-400 KHz) OK

Previous results: PCB1 always works, forwarding always fails regardless
of encoding (4-bit, 8-bit, 10-bit, 16-bit all tried). Datasheet says
chip auto-regenerates on DO — forwarding failure likely hardware.

Section 3 includes hardware diagnostics to narrow down the issue.

Setup: Pi -> SN74AHCT125N -> PCB1 -> PCB2 -> PCB3 -> PCB4
       (10k pull-up on GPIO 20 to 3.3V)
"""

import math
import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4
RESET_TARGET_US = 250


def reset_bytes_for_speed(spi_speed):
    return max(math.ceil(RESET_TARGET_US * spi_speed / 8_000_000), 50)


# === 4-bit encoding (500 KHz at 2.0 MHz — outside spec but works for PCB1) ===

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


# === 16-bit encoding (250 KHz at 4.0 MHz — within spec, byte-aligned) ===

def encode_byte_16bit(value):
    """Each data bit -> 2 SPI bytes. LOW pulse in byte 1, byte 2 = 0xFF."""
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
    """Build frame: [Reset][C1][C2][D1..Dn][Reset]"""
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for pixel in pixels:
        for channel in pixel:
            buf += lut[channel]
    buf += b'\xFF' * reset_bytes
    return buf


def build_frame_direct(c1_bytes, c2_bytes, pixel, lut, reset_bytes):
    """Build frame with single pixel for direct-to-chip testing."""
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for channel in pixel:
        buf += lut[channel]
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
          f"actual {actual/1e6:.3f} MHz, mode {spi_mode}")
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


C1_DEFAULT = [0x1E, 0x1E, 0x1E, 0x1E]
C2_DEFAULT = [0xE1, 0xE1, 0xE1, 0xE1]


TESTS = [
    # =================================================================
    # SECTION 1: 16-bit @ 4.0 MHz — in-spec timing (primary)
    # T0L=750ns T1L=1500ns Period=4us=250KHz — all within TM1815B spec
    # =================================================================
    {
        "section": "\n  === SECTION 1: 16-bit @ 4.0 MHz (in-spec, byte-aligned) ===",
        "name": "Unique: PCB1=RED PCB2=GREEN PCB3=BLUE PCB4=WHITE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "All RED",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "All GREEN",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },
    {
        "name": "All BLUE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 0, 0, 255)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 2: Color order diagnostic
    # TM1815B datasheet says WRGB, but Adafruit TM1814 guide uses GRBW.
    # Send 255 in one byte position at a time. Report which color you see.
    # =================================================================
    {
        "section": "\n  === SECTION 2: Color order diagnostic ===\n"
                   "  Each test sets ONE byte to 255, rest to 0, on ALL PCBs.\n"
                   "  Report what color appears (red/green/blue/white/none).",
        "name": "Byte 0 = 255 (should be WHITE if WRGB)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(255, 0, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "Byte 1 = 255 (should be RED if WRGB)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "Byte 2 = 255 (should be GREEN if WRGB)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },
    {
        "name": "Byte 3 = 255 (should be BLUE if WRGB)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 0, 0, 255)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 3: Hardware diagnostics for forwarding
    #
    # TEST A: Send data for only 1 pixel. Per datasheet, chip 1 consumes
    # C1+C2+D1 and forwards nothing (no D2-D4). If PCBs 2-4 keep
    # cycling, it confirms they aren't receiving valid data from DO.
    #
    # TEST B: Send data for 2 pixels only. If PCB2 responds, forwarding
    # works. If not, the DO->DIN link is broken.
    #
    # MANUAL TESTS (suggested after running this script):
    # 1. Move the Pi's data wire from PCB1 DIN directly to PCB2 DIN.
    #    Run Section 1 tests. If PCB2 shows colors, PCB2's chip works
    #    and the issue is the PCB1 DO -> PCB2 DIN connection.
    # 2. Check PCB1 DO (pin 5) with a multimeter:
    #    - Idle (no data): should be HIGH (~5V)
    #    - During data: should see voltage activity
    # 3. Check PCB2 VDD: should be ~5V
    # 4. Check 100nF decoupling caps near each chip's VDD pin
    # =================================================================
    {
        "section": "\n  === SECTION 3: Forwarding diagnostics ===\n"
                   "  These tests help identify if forwarding failure is hardware.",
        "name": "DIAG A: 1-pixel frame (only PCB1 should light)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 255, 0, 0)],
        "note": "PCB1 should show RED. PCBs 2-4 should cycle (no data for them).",
    },
    {
        "name": "DIAG B: 2-pixel frame (PCB1=RED, PCB2=GREEN?)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0)],
        "note": "If PCB2 shows GREEN, forwarding works! If cycling, DO->DIN link may be broken.",
    },
    {
        "name": "DIAG C: 2-pixel frame (PCB1=BLUE, PCB2=WHITE?)",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 0, 0, 255), (255, 0, 0, 0)],
        "note": "Different colors to rule out coincidence.",
    },

    # =================================================================
    # SECTION 4: 4-bit @ 2.0 MHz baseline (known PCB1 works)
    # 500 KHz — above 400 KHz spec but PCB1 tolerates it.
    # =================================================================
    {
        "section": "\n  === SECTION 4: 4-bit @ 2.0 MHz baseline ===",
        "name": "4-bit 2.0 MHz — unique colors (PCB1 should work)",
        "speed": 2_000_000,
        "encoding": "4bit",
        "c1": C1_DEFAULT,
        "c2": C2_DEFAULT,
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 5: All off
    # =================================================================
    {
        "section": "\n  === SECTION 5: All off ===",
        "name": "All off",
        "speed": 4_000_000,
        "encoding": "16bit",
        "c1": [0x00, 0x00, 0x00, 0x00],
        "c2": [0xFF, 0xFF, 0xFF, 0xFF],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — Verified Against Datasheet")
    print(f"  {NUM_LEDS} PCBs: PCB1 -> PCB2 -> PCB3 -> PCB4")
    print()
    print("  Confirmed correct per TM1815B datasheet:")
    print("    Frame: [Reset][C1][C2][D1][D2]...[Dn][Reset] (global C1/C2)")
    print("    Pixel order: W, R, G, B (8 bits each, MSB first)")
    print("    Timing: T0L 620-820ns, T1L 1300-2000ns, 200-400 KHz")
    print("    Reset: HIGH >= 200us")
    print()
    print("  16-bit encoding @ 4.0 MHz:")
    print("    T0L=750ns  T1L=1500ns  Period=4us=250KHz  ALL IN SPEC")
    print()
    print("  Previous result: PCB1 works, forwarding fails with every")
    print("  encoding tested. Section 3 has diagnostics to determine")
    print("  if this is a hardware issue.")
    print()
    print("  C2 = ~C1 (validated). Press Enter to START/STOP each test.")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]
        speed = test["speed"]
        encoding = test.get("encoding", "16bit")

        for j in range(4):
            if c2[j] != (c1[j] ^ 0xFF):
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} != "
                      f"~C1[{j}]=0x{c1[j]:02X} ***")
                sys.exit(1)

        if encoding == "16bit":
            lut = LUT_16BIT
            t0l, t1l = 750, 1500
            spi_bits_per = 16
        else:
            lut = LUT_4BIT
            bit_ns = int(1e9 / speed)
            t0l, t1l = bit_ns, bit_ns * 3
            spi_bits_per = 4

        reset = reset_bytes_for_speed(speed)
        reset_us = reset * 8 / speed * 1e6

        frame = build_frame(c1, c2, pixels, lut, reset)

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: {spi_bits_per}-bit | SPI: {speed/1e6:.1f} MHz "
              f"| T0L={t0l}ns T1L={t1l}ns")
        print(f"         Frame: {len(frame)} bytes ({len(pixels)} pixels) | "
              f"Reset: {reset_us:.0f}us")
        print(f"         C1={fmt_c(c1)}  C2={fmt_c(c2)}")
        for pi, px in enumerate(pixels, 1):
            print(f"         D{pi}: W={px[0]:>3} R={px[1]:>3} G={px[2]:>3} B={px[3]:>3}")

        if "note" in test:
            print(f"         NOTE: {test['note']}")

        input("         Press Enter to start...")
        frames = run_test(list(frame), speed)
        print(f"         Sent {frames} frames")

    print()
    print("=" * 68)
    print("  RESULTS SUMMARY")
    print("=" * 68)
    print()
    print("  Section 1 — Did PCB1 show correct colors?")
    print("  Section 2 — Which byte position maps to which color?")
    print("    Byte 0 = ___  Byte 1 = ___  Byte 2 = ___  Byte 3 = ___")
    print()
    print("  Section 3 — Forwarding diagnostics:")
    print("    DIAG A (1-pixel): PCB1 lit? PCBs 2-4 cycling?")
    print("    DIAG B (2-pixel): Did PCB2 show GREEN?")
    print("    DIAG C (2-pixel): Did PCB2 show WHITE?")
    print()
    print("  If PCB2 never shows colors in DIAG B/C, try these HW checks:")
    print("    1. Move Pi data wire directly to PCB2 DIN (bypass PCB1)")
    print("       -> If PCB2 works, the PCB1 DO -> PCB2 DIN link is bad")
    print("    2. Multimeter on PCB1 DO (pin 5): idle should be ~5V")
    print("    3. Check PCB2 has power (VDD ~5V, GND connected)")
    print("    4. Check 100nF decoupling cap near each chip VDD")
    print("    5. Datasheet recommends 100ohm series R on DIN and DO")
    print("=" * 68)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
