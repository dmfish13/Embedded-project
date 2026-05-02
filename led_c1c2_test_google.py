#!/usr/bin/env python3
"""
C1/C2 forwarding test — 16-bit encoding at 4.0 MHz for gapless timing.

The Pi's hardware SPI inserts micro-delays between bytes. 10-bit encoding 
failed because byte boundaries (every 8 bits) fell in the middle of the 
data LOW pulses, stretching them out of spec. 

16-bit encoding perfectly aligns SPI bytes to data bits, guaranteeing
the line is always HIGH during an inter-byte gap.
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


# === 4-bit encoding (baseline, PCB1 only) ===
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


# === 16-bit encoding (The Pi SPI Gap Bypass) ===
def encode_byte_16bit(value):
    """Encode one data byte (8 bits) into 16 SPI bytes.
    
    16 SPI bits per data bit. At 4.0 MHz, 1 SPI bit = 250ns.
    Period = 16 * 250 = 4.0 us (Inside 2.5 - 5.0 us spec).
    
    Logic 0: 3 LOW + 13 HIGH = 750ns LOW -> 0x1F, 0xFF
    Logic 1: 6 LOW + 10 HIGH = 1500ns LOW -> 0x03, 0xFF
    """
    result = bytearray(16)
    for i in range(8):
        # MSB first
        if value & (1 << (7 - i)):
            # Logic 1: 6 LOWs
            result[i*2] = 0x03
            result[i*2+1] = 0xFF
        else:
            # Logic 0: 3 LOWs
            result[i*2] = 0x1F
            result[i*2+1] = 0xFF
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
    # SECTION 1: 16-bit @ 4.0 MHz, SPI Mode 0 
    # =================================================================
    {
        "section": "\n  === SECTION 1: 16-bit @ 4.0 MHz, Mode 0 ===",
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
    # =================================================================
    # SECTION 2: 16-bit @ 4.0 MHz, SPI Mode 3 
    # =================================================================
    {
        "section": "\n  === SECTION 2: 16-bit @ 4.0 MHz, Mode 3 (idle HIGH) ===",
        "name": "Mode 3 — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 3,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    # =================================================================
    # SECTION 3: Individual PCB addressing @ 4.0 MHz 16-bit
    # =================================================================
    {
        "section": "\n  === SECTION 3: 16-bit 4.0 MHz — one PCB at a time (Mode 3) ===",
        "name": "Only PCB1 = RED",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 3,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB2 = GREEN",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 3,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 0, 0, 0), (0, 0, 255, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    # =================================================================
    # SECTION 4: 4-bit @ 2.0 MHz baseline 
    # =================================================================
    {
        "section": "\n  === SECTION 4: 4-bit @ 2.0 MHz baseline ===",
        "name": "4-bit 2.0 MHz Mode 0 — unique colors (PCB1 should work)",
        "speed": 2_000_000,
        "encoding": "4bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
]

def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — 16-Bit Hardware Gap Bypass")
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
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT ~C1")
                sys.exit(1)

        lut = LUT_16BIT if encoding == "16bit" else LUT_4BIT
        reset = reset_bytes_for_speed(speed)
        reset_us = reset * 8 / speed * 1e6

        if encoding == "16bit":
            t0_ns = int(1e9 / speed * 3)
            t1_ns = int(1e9 / speed * 6)
            bits_per = 16
        else:
            t0_ns = int(1e9 / speed * 1)
            t1_ns = int(1e9 / speed * 3)
            bits_per = 4

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         Encoding: {bits_per}-bit | SPI: {speed/1e6:.1f} MHz "
              f"Mode {spi_mode} | 0 LOW: {t0_ns}ns | 1 LOW: {t1_ns}ns")
        frame = build_frame(c1, c2, pixels, lut, reset)
        print(f"         Frame: {len(frame)} bytes | "
              f"Reset: {reset} bytes = {reset_us:.0f}µs | C2 == ~C1: verified")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        frames = run_test(list(frame), speed, spi_mode)
        print(f"         Sent {frames} frames")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")