#!/usr/bin/env python3
"""
TM1815B C1/C2 Forwarding Test — Proper Return-to-Zero (RZ) Encoding
"""

import math
import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4
RESET_TARGET_US = 300

def reset_bytes_for_speed(spi_speed):
    """Calculate minimum 0x00 bytes needed for a LOW reset."""
    bytes_needed = math.ceil(RESET_TARGET_US * spi_speed / 8_000_000)
    return max(bytes_needed, 50)

# === 16-bit Return-to-Zero Encoding @ 4.0 MHz ===
def encode_byte_16bit_rz(value):
    """
    At 4.0 MHz, 1 SPI bit = 250ns. 16 bits = 4.0 us period.
    Logic 0: 3 HIGH + 13 LOW = 750ns HIGH -> 0xE0, 0x00
    Logic 1: 6 HIGH + 10 LOW = 1500ns HIGH -> 0xFC, 0x00
    """
    result = bytearray(16)
    for i in range(8):
        if value & (1 << (7 - i)):
            # Logic 1
            result[i*2] = 0xFC
            result[i*2+1] = 0x00
        else:
            # Logic 0
            result[i*2] = 0xE0
            result[i*2+1] = 0x00
    return bytes(result)

LUT_16BIT_RZ = [encode_byte_16bit_rz(v) for v in range(256)]

# === 8-bit Return-to-Zero Encoding @ 2.0 MHz ===
def encode_byte_8bit_rz(value):
    """
    At 2.0 MHz, 1 SPI bit = 500ns. 8 bits = 4.0 us period.
    Logic 0: 1 HIGH + 7 LOW = 500ns HIGH -> 0x80
    Logic 1: 3 HIGH + 5 LOW = 1500ns HIGH -> 0xE0
    """
    result = bytearray(8)
    for i in range(8):
        if value & (1 << (7 - i)):
            # Logic 1
            result[i] = 0xE0
        else:
            # Logic 0
            result[i] = 0x80
    return bytes(result)

LUT_8BIT_RZ = [encode_byte_8bit_rz(v) for v in range(256)]

def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes):
    """Build a TM1815B RZ frame. Reset is now 0x00 (LOW)."""
    buf = bytearray(b'\x00' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\x00' * reset_bytes
    return buf

def run_test(buf_list, spi_speed, spi_mode=0):
    """Send continuously until Enter is pressed."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
    spi.mode = spi_mode  # Must be 0 to idle LOW
    spi.lsbfirst = False

    frame_count = 0
    running = True

    print(f"         Requested {spi_speed/1e6:.1f} MHz, SPI mode {spi_mode}")
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

UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    {
        "name": "Test 1: 16-bit 4.0 MHz RZ — PCB1=RED, PCB2=GRN, PCB3=BLU, PCB4=WHT",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    },
    {
        "name": "Test 2: 16-bit 4.0 MHz RZ — All RED",
        "speed": 4_000_000,
        "encoding": "16bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "Test 3: 8-bit 2.0 MHz RZ — PCB1=RED, PCB2=GRN, PCB3=BLU, PCB4=WHT",
        "speed": 2_000_000,
        "encoding": "8bit",
        "spi_mode": 0,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    }
]

def main():
    print("=" * 68)
    print("  TM1815B Forwarding Test — Proper RZ Encoding (LOW Reset)")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]
        speed = test["speed"]
        encoding = test["encoding"]
        spi_mode = test["spi_mode"]

        # Safety check: C2 must be ~C1
        for j in range(4):
            if c2[j] != (c1[j] ^ 0xFF):
                print(f"  *** ERROR: C2[{j}]=0x{c2[j]:02X} is NOT ~C1")
                sys.exit(1)

        lut = LUT_16BIT_RZ if encoding == "16bit" else LUT_8BIT_RZ
        reset = reset_bytes_for_speed(speed)
        reset_us = reset * 8 / speed * 1e6

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        
        frame = build_frame(c1, c2, pixels, lut, reset)
        print(f"         Frame: {len(frame)} bytes | Reset: {reset} bytes (0x00) = {reset_us:.0f}µs")
        
        input("         Press Enter to start...")
        frames = run_test(list(frame), speed, spi_mode)
        print(f"         Sent {frames} frames")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")