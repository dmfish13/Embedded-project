#!/usr/bin/env python3
"""
TM1815B / TM1814 Forwarding Test 
8-Bit Return-to-One (RTO) Encoding at 8.0 MHz WITH 32-BIT WORD SIZE HACK

Fix: Bypasses Raspberry Pi 8-bit SPI micro-pauses by packing data into 
32-bit words, keeping the signal gapless for longer durations to sync 
with the chip's internal 800 KHz signal regenerator.
"""

import sys
import time
import threading
from spidev import SpiDev

NUM_LEDS = 4

# === 8-bit Return-to-One Encoding @ 8.0 MHz ===
def encode_byte_8bit_8mhz(value):
    """
    At 8.0 MHz, 1 SPI bit = 125ns. 8 bits = 1.0 us period.
    Logic 0: 3 LOW (375ns) + 5 HIGH = 0x1F
    Logic 1: 6 LOW (750ns) + 2 HIGH = 0x03
    """
    result = bytearray(8)
    for i in range(8):
        if value & (1 << (7 - i)):
            result[i] = 0x03
        else:
            result[i] = 0x1F
    return bytes(result)

LUT_8BIT_8MHZ = [encode_byte_8bit_8mhz(v) for v in range(256)]

def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes=300):
    """Builds the raw byte array frame."""
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf

def pack_to_32bit_words(byte_buf):
    """
    Pads the byte array to a multiple of 4 and packs it into 32-bit integers.
    The SPI controller shifts out the MSB of the 32-bit word first.
    """
    # Pad with 0xFF (Idle HIGH) so the length is perfectly divisible by 4
    while len(byte_buf) % 4 != 0:
        byte_buf.append(0xFF)
        
    words_32bit = []
    for i in range(0, len(byte_buf), 4):
        # Pack 4 bytes into one 32-bit int. 
        # byte_buf[i] goes to the highest 8 bits so it gets transmitted first.
        word = (byte_buf[i] << 24) | (byte_buf[i+1] << 16) | (byte_buf[i+2] << 8) | byte_buf[i+3]
        words_32bit.append(word)
        
    return words_32bit

def run_test(words_list, spi_speed, spi_mode=0):
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
    spi.mode = spi_mode  
    spi.lsbfirst = False
    
    # THE HACK: Tell the SPI hardware to process in 32-bit chunks
    try:
        spi.bits_per_word = 32
    except OSError as e:
        print(f"\n  *** FATAL ERROR: Your Pi's SPI driver rejected 32-bit words ({e}). ***")
        print("  *** You may need to update your kernel or use the PWM/I2S method. ***\n")
        sys.exit(1)

    frame_count = 0
    running = True

    print(f"         Requested {spi_speed/1e6:.1f} MHz | 32-bit Word Mode Enabled")
    print("         Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait, daemon=True)
    t.start()

    # Continuous background writing to suppress the demo cycle
    while running:
        spi.xfer2(words_list)
        frame_count += 1
        time.sleep(0.002) 

    spi.close()
    return frame_count

UNIQUE = [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)]

TESTS = [
    {
        "name": "Test 1: 8.0 MHz | 32-bit Packed Words",
        "speed": 8_000_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "c2": [0xE1, 0xE1, 0xE1, 0xE1],
        "pixels": UNIQUE,
    }
]

def main():
    print("=" * 68)
    print("  TM1815B/TM1814 Forwarding Test — 32-Bit Word Hack @ 8.0 MHz")
    print("=" * 68)

    for i, test in enumerate(TESTS, 1):
        c1 = test["c1"]
        c2 = test["c2"]
        pixels = test["pixels"]
        speed = test["speed"]

        lut = LUT_8BIT_8MHZ
        reset_bytes = 300 
        
        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        
        # Build the byte array, then pack it into 32-bit words
        raw_frame = build_frame(c1, c2, pixels, lut, reset_bytes)
        packed_frame = pack_to_32bit_words(raw_frame)
        
        input("         Press Enter to start...")
        frames = run_test(packed_frame, speed, spi_mode=0)
        print(f"         Sent {frames} frames")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")