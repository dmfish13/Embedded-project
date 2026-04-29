#!/usr/bin/env python3
"""
Continuous LED test — sends TM1815B frames in a tight loop.

Key fixes over previous attempts:
  1. Leading preamble is 80 bytes of 0xFF (400 µs at 1.6 MHz),
     well above the 280 µs reset minimum.
  2. Frames are sent continuously in a tight loop so MOSI never
     idles LOW between transfers.
  3. Tests multiple SPI speeds to find the one the Pi 5 actually
     produces closest to the target data rate.

Usage:
    python3 led_continuous_test.py
"""

import time
import sys
from spidev import SpiDev

NUM_LEDS = 4

# --- TM1815B inverted encoding (4 SPI bits per data bit) ---

def encode_byte_inv(value):
    """Logic 1 -> 0b0001 (long LOW 1875ns), Logic 0 -> 0b0111 (short LOW 625ns)."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001
        else:
            encoded = (encoded << 4) | 0b0111
    return bytes([
        (encoded >> 24) & 0xFF,
        (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF,
        encoded & 0xFF,
    ])


LUT_INV = [encode_byte_inv(v) for v in range(256)]


def build_frame_tm1815b(r, g, b, w, num_leds, current=10,
                        preamble_bytes=80, reset_bytes=80):
    """Build a complete TM1815B frame with generous reset margins."""
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([b_ ^ 0xFF for b_ in c1])

    buf = bytearray(b'\xFF' * preamble_bytes)

    for bv in c1:
        buf += LUT_INV[bv]
    for bv in c2:
        buf += LUT_INV[bv]

    for _ in range(num_leds):
        buf += LUT_INV[w] + LUT_INV[r] + LUT_INV[g] + LUT_INV[b]

    buf += b'\xFF' * reset_bytes

    return buf


# --- Non-inverted WS2812-style encoding (for comparison) ---

def encode_byte_ws(value):
    """Data 1 -> 0b1110 (HIGH 3, LOW 1), Data 0 -> 0b1000 (HIGH 1, LOW 3)."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b1110
        else:
            encoded = (encoded << 4) | 0b1000
    return bytes([
        (encoded >> 24) & 0xFF,
        (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF,
        encoded & 0xFF,
    ])


LUT_WS = [encode_byte_ws(v) for v in range(256)]


def build_frame_ws_wrgb(r, g, b, w, num_leds,
                        preamble_bytes=0, reset_bytes=80):
    """WS2812-style non-inverted, WRGB order, no C1/C2."""
    buf = bytearray(b'\x00' * preamble_bytes)
    for _ in range(num_leds):
        buf += LUT_WS[w] + LUT_WS[r] + LUT_WS[g] + LUT_WS[b]
    buf += b'\x00' * reset_bytes
    return buf


def build_frame_ws_grbw(r, g, b, w, num_leds,
                        preamble_bytes=0, reset_bytes=80):
    """WS2812-style non-inverted, GRBW order (SK6812), no C1/C2."""
    buf = bytearray(b'\x00' * preamble_bytes)
    for _ in range(num_leds):
        buf += LUT_WS[g] + LUT_WS[r] + LUT_WS[b] + LUT_WS[w]
    buf += b'\x00' * reset_bytes
    return buf


# --- Test configurations ---

TESTS = [
    # TM1815B inverted at various SPI speeds
    {
        "name": "TM1815B inverted, 1.6 MHz (standard 400kHz data rate)",
        "speed": 1_600_000,
        "builder": lambda: build_frame_tm1815b(255, 0, 0, 0, NUM_LEDS),
        "desc": "Red, inverted encoding, WRGB, C1/C2",
    },
    {
        "name": "TM1815B inverted, 2.0 MHz",
        "speed": 2_000_000,
        "builder": lambda: build_frame_tm1815b(255, 0, 0, 0, NUM_LEDS),
        "desc": "Red, inverted encoding, slightly faster",
    },
    {
        "name": "TM1815B inverted, 1.2 MHz",
        "speed": 1_200_000,
        "builder": lambda: build_frame_tm1815b(255, 0, 0, 0, NUM_LEDS),
        "desc": "Red, inverted encoding, slightly slower",
    },
    {
        "name": "TM1815B inverted, 3.2 MHz (800kHz data rate)",
        "speed": 3_200_000,
        "builder": lambda: build_frame_tm1815b(0, 255, 0, 0, NUM_LEDS),
        "desc": "Green, inverted encoding, 800kHz variant",
    },
    {
        "name": "TM1815B inverted, 2.4 MHz",
        "speed": 2_400_000,
        "builder": lambda: build_frame_tm1815b(0, 0, 255, 0, NUM_LEDS),
        "desc": "Blue, inverted encoding, mid-speed",
    },
    # Non-inverted WS2812-style at different speeds
    {
        "name": "WS-style non-inverted WRGB, 1.6 MHz",
        "speed": 1_600_000,
        "builder": lambda: build_frame_ws_wrgb(255, 0, 0, 0, NUM_LEDS),
        "desc": "Red, non-inverted, WRGB, no C1/C2",
    },
    {
        "name": "WS-style non-inverted GRBW, 3.2 MHz",
        "speed": 3_200_000,
        "builder": lambda: build_frame_ws_grbw(0, 0, 0, 255, NUM_LEDS,
                                               reset_bytes=120),
        "desc": "White, non-inverted, GRBW (SK6812 style), 800kHz",
    },
    {
        "name": "TM1815B inverted, max current (63), 1.6 MHz",
        "speed": 1_600_000,
        "builder": lambda: build_frame_tm1815b(255, 255, 255, 255, NUM_LEDS,
                                               current=63),
        "desc": "All white, max current, inverted",
    },
]


def run_test(test, duration=5.0):
    """Send frames continuously for `duration` seconds."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = test["speed"]
    spi.mode = 0b00
    spi.lsbfirst = False

    buf = test["builder"]()
    buf_list = list(buf)
    frame_count = 0

    start = time.monotonic()
    while time.monotonic() - start < duration:
        spi.xfer2(buf_list)
        frame_count += 1

    spi.close()
    return frame_count


def main():
    print("=" * 60)
    print("  Continuous LED Test — TM1815B Protocol Sweep")
    print(f"  {NUM_LEDS} LEDs, {len(TESTS)} configurations")
    print("  Each test sends frames continuously for 5 seconds.")
    print("  Watch the LEDs — note which test changes them.")
    print("=" * 60)

    input("\n  Press Enter to begin...\n")

    for i, test in enumerate(TESTS, 1):
        print(f"  [{i}/{len(TESTS)}] {test['name']}")
        print(f"           {test['desc']}")
        sys.stdout.flush()

        frames = run_test(test, duration=5.0)
        print(f"           Sent {frames} frames in 5 seconds")

        # Brief pause between tests — hold line HIGH via a short 0xFF burst
        spi = SpiDev()
        spi.open(1, 0)
        spi.max_speed_hz = test["speed"]
        spi.mode = 0b00
        spi.xfer2([0xFF] * 200)
        spi.close()

        print(f"           Pausing 3 seconds before next test...")
        time.sleep(3)
        print()

    print("  Done. Which test (if any) changed the LEDs?")
    print()
    print("  If NONE worked, the issue may be:")
    print("    - MOSI idles LOW between xfer2 calls (corrupts reset)")
    print("    - The LED IC is not a standard TM1815B")
    print("    - The LED PCB has its own controller overriding DI")


if __name__ == "__main__":
    main()
