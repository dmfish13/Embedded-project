#!/usr/bin/env python3
"""
TM1815B / TM1814 Forwarding Test 
16-Bit Return-to-One (RTO) Encoding at 8.0 MHz

Fixes applied based on TM1814 specs:
1. Shortened LOW pulses: T0L = 375ns, T1L = 750ns.
2. SPI Speed: 8.0 MHz prevents Pi hardware byte gap corruption 
   by ensuring all gaps occur during the HIGH phase of the bit.
"""

import sys
import time
import threading
from spidev import SpiDev

NUM_LEDS = 4

# === 16-bit Return-to-One Encoding @ 8.0 MHz ===
def encode_byte_16bit_8mhz(value):
    """
    At 8.0 MHz, 1 SPI bit = 125ns. 16 bits = 2.0 us period.
    Logic 0: 3 LOW (375ns) + 13 HIGH = 0x1F, 0xFF
    Logic 1: 6 LOW (750ns) + 10 HIGH = 0x03, 0xFF
    """
    result = bytearray(16)
    for i in range(8):
        if value & (1 << (7 - i)):
            # Logic 1: 6 LOWs
            result[i*2] = 0x03
            result[i*2+1] = 0xFF
        else:
            # Logic 0: 3 LOWs
            result[i*2] = 0x1F
            result[i*2+1] = 0xFF
    return bytes(result)

LUT_16BIT_8MHZ = [encode_byte_16bit_8mhz(v) for v in range(256)]

def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes=300):
    """
    Reset is 0xFF (HIGH). 
    300 bytes at 8.0 MHz = 2400 bits = 300us guaranteed reset.
    """
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
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
    spi.mode = spi_mode  
    spi.lsbfirst = False

    frame_count = 0
    running = True

    print(f"         Requested {spi_speed/1e6:.1f} MHz")
    print("         Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait, daemon=True)
    t.start()

    # The TM1814 requires continuous background writing to suppress the demo cycle
    while running:
        spi.xfer2(buf_list)
        frame_count += 1
        time.sleep(0.002) # Tiny 2ms delay to prevent CPU slamming, safely below 500ms demo timeout

    spi.close()
    return frame_count

UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    {
        "name": "Test 1: 16-bit 8.0 MHz RTO (Perfect Timing Match)",
        "speed": 8_000_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    }
]

def main():
    print("=" * 68)
    print("  TM1815B/TM1814 Forwarding Test — 16-Bit @ 8.0 MHz")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]
        speed = test["speed"]

        lut = LUT_16BIT_8MHZ
        reset_bytes = 300 
        
        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        frame = build_frame(c1, c2, pixels, lut, reset_bytes)
        
        input("         Press Enter to start...")
        frames = run_test(list(frame), speed, spi_mode=0)
        print(f"         Sent {frames} frames")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")