#!/usr/bin/env python3
"""
Reset-then-send test — holds DIN HIGH for an extended period to force
all TM1815B chips in the chain into reset, then immediately sends
data before demo mode can re-activate.

Also tests parallel-friendly mode (NUM_LEDS=1) for star topology.

Usage:
    python3 led_reset_test.py
"""

import time
import sys
import subprocess
import threading
from spidev import SpiDev

NUM_LEDS = 4
SPI_SPEED = 2_000_000


def encode_byte(value):
    """Logic 1 -> 0b0001 (long LOW), Logic 0 -> 0b0111 (short LOW)."""
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


LUT = [encode_byte(v) for v in range(256)]


def build_frame(r, g, b, w, num_leds, current=30,
                preamble_bytes=80, reset_bytes=100):
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([c1_val ^ 0xFF] * 4)

    buf = bytearray(b'\xFF' * preamble_bytes)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for _ in range(num_leds):
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
    buf += b'\xFF' * reset_bytes
    return buf


def gpio_high(pin=20):
    """Set GPIO 20 to output HIGH (hold DIN in reset state)."""
    subprocess.run(["pinctrl", "set", str(pin), "op", "dh"],
                   capture_output=True)


def gpio_to_spi(pin=20):
    """Return GPIO 20 to SPI1 MOSI alt function."""
    subprocess.run(["pinctrl", "set", str(pin), "a5"],
                   capture_output=True)


def hold_reset(seconds):
    """Hold DIN HIGH via GPIO for a specified duration."""
    gpio_high(20)
    print(f"    Holding DIN HIGH (reset) for {seconds} seconds...")
    time.sleep(seconds)


def send_continuous(r, g, b, w, num_leds, label=""):
    """Send frames continuously until Enter is pressed."""
    gpio_to_spi(20)

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False

    buf = list(build_frame(r, g, b, w, num_leds, current=30))
    frame_count = 0
    running = True

    if label:
        print(f"    {label}")
    print("    Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait_for_enter():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait_for_enter, daemon=True)
    t.start()

    while running:
        spi.xfer2(buf)
        frame_count += 1

    spi.close()
    gpio_high(20)
    return frame_count


def main():
    print("=" * 62)
    print("  Reset-Then-Send LED Test")
    print("  Tests long reset periods before sending data")
    print("=" * 62)

    tests = [
        {
            "name": "1-second reset, then RED to 4 LEDs",
            "reset_s": 1.0,
            "r": 255, "g": 0, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then RED to 4 LEDs",
            "reset_s": 5.0,
            "r": 255, "g": 0, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then GREEN to 4 LEDs",
            "reset_s": 5.0,
            "r": 0, "g": 255, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then ALL OFF to 4 LEDs",
            "reset_s": 5.0,
            "r": 0, "g": 0, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then WHITE (W channel) to 4 LEDs",
            "reset_s": 5.0,
            "r": 0, "g": 0, "b": 0, "w": 255,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then RED to 8 LEDs (extra data)",
            "reset_s": 5.0,
            "r": 255, "g": 0, "b": 0, "w": 0,
            "num_leds": 8,
        },
    ]

    # Start with GPIO HIGH
    gpio_high(20)
    time.sleep(0.5)

    for i, test in enumerate(tests, 1):
        print(f"\n  [{i}/{len(tests)}] {test['name']}")
        input("    Press Enter to start...")

        hold_reset(test["reset_s"])

        frames = send_continuous(
            test["r"], test["g"], test["b"], test["w"],
            test["num_leds"],
            label=f"RGBW=({test['r']},{test['g']},{test['b']},{test['w']}) "
                  f"x{test['num_leds']} LEDs"
        )
        print(f"    Sent {frames} frames")
        time.sleep(2)

    print("\n  Done.")
    print("  Key observations:")
    print("    - Did the long reset stop the cycling before data was sent?")
    print("    - Did any test light up ALL 4 series LEDs?")
    print("    - Did the 'ALL OFF' test turn any LEDs off?")


if __name__ == "__main__":
    main()
