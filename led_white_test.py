#!/usr/bin/env python3
"""
White LED channel diagnostic — determines how the TM1815B's OUTW
drives the white LED diode on the PCB.

Tests W channel independently, at different PWM and current levels,
and in combination with RGB. Each test sends continuously until
Enter is pressed.

The PCB has a transistor (Q1) between OUTW and the white LED, so
the drive behavior may differ from the RGB channels.

Usage:
    python3 led_white_test.py
"""

import subprocess
import sys
import threading
from spidev import SpiDev

subprocess.run(["pinctrl", "set", "20", "a5"], capture_output=True)

NUM_LEDS = 4
SPI_SPEED = 2_000_000


def encode_byte(value):
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


def build_frame(w, r, g, b, num_leds,
                current_w=30, current_r=30, current_g=30, current_b=30,
                preamble=80, reset=100):
    """Build frame with independent per-channel current control."""
    c1 = bytes([current_w & 0x3F, current_r & 0x3F,
                current_g & 0x3F, current_b & 0x3F])
    c2 = bytes([v ^ 0xFF for v in c1])

    buf = bytearray(b'\xFF' * preamble)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for _ in range(num_leds):
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
    buf += b'\xFF' * reset
    return buf


def run_test(buf_list):
    """Send continuously until Enter is pressed. Returns frame count."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False

    frame_count = 0
    running = True

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


TESTS = [
    # --- Section 1: W channel only (RGB off) ---
    {
        "name": "W=255, RGB=0  (White LED only, full PWM)",
        "w": 255, "r": 0, "g": 0, "b": 0,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=128, RGB=0  (White LED half PWM)",
        "w": 128, "r": 0, "g": 0, "b": 0,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=50, RGB=0  (White LED low PWM)",
        "w": 50, "r": 0, "g": 0, "b": 0,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },

    # --- Section 2: W current sweep (PWM fixed at 255) ---
    {
        "name": "W=255, W current=63 (max), RGB off",
        "w": 255, "r": 0, "g": 0, "b": 0,
        "cw": 63, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=255, W current=10 (low), RGB off",
        "w": 255, "r": 0, "g": 0, "b": 0,
        "cw": 10, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=255, W current=0 (minimum 6.5mA), RGB off",
        "w": 255, "r": 0, "g": 0, "b": 0,
        "cw": 0, "cr": 30, "cg": 30, "cb": 30,
    },

    # --- Section 3: RGB only (W off) — baseline ---
    {
        "name": "W=0, R=255  (Red only, no white)",
        "w": 0, "r": 255, "g": 0, "b": 0,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=0, R=255 G=255 B=255  (RGB white, no W)",
        "w": 0, "r": 255, "g": 255, "b": 255,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },

    # --- Section 4: W + RGB combinations ---
    {
        "name": "W=255, R=255  (White + Red)",
        "w": 255, "r": 255, "g": 0, "b": 0,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=255, R=255 G=255 B=255  (White + full RGB)",
        "w": 255, "r": 255, "g": 255, "b": 255,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },
    {
        "name": "W=128, R=128 G=128 B=128  (half W + half RGB)",
        "w": 128, "r": 128, "g": 128, "b": 128,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },

    # --- Section 5: All channels off ---
    {
        "name": "W=0 R=0 G=0 B=0  (all off)",
        "w": 0, "r": 0, "g": 0, "b": 0,
        "cw": 30, "cr": 30, "cg": 30, "cb": 30,
    },
]


def main():
    print("=" * 62)
    print("  TM1815B White LED Channel Diagnostic")
    print(f"  {NUM_LEDS} LEDs @ {SPI_SPEED/1e6:.1f} MHz SPI")
    print("  Press Enter to START each test, Enter again to STOP.")
    print()
    print("  D packet format: W[7:0] R[7:0] G[7:0] B[7:0]")
    print("  C1 current:      W[5:0] R[5:0] G[5:0] B[5:0]")
    print("  PCB: OUTW -> Q1 transistor -> White LED (LED2)")
    print("       OUTR/G/B -> RGB LED (LED1)")
    print("=" * 62)

    for i, test in enumerate(TESTS, 1):
        section = ""
        if i == 1:
            section = "\n  --- W channel only (RGB off) ---"
        elif i == 4:
            section = "\n  --- W current sweep (PWM=255) ---"
        elif i == 7:
            section = "\n  --- RGB only (W off) — baseline ---"
        elif i == 9:
            section = "\n  --- W + RGB combinations ---"
        elif i == 12:
            section = "\n  --- All off ---"
        if section:
            print(section)

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         PWM:     W={test['w']:>3}  R={test['r']:>3}"
              f"  G={test['g']:>3}  B={test['b']:>3}")
        print(f"         Current: W={test['cw']:>3}  R={test['cr']:>3}"
              f"  G={test['cg']:>3}  B={test['cb']:>3}")

        input("         Press Enter to start...")

        buf = build_frame(
            test["w"], test["r"], test["g"], test["b"], NUM_LEDS,
            current_w=test["cw"], current_r=test["cr"],
            current_g=test["cg"], current_b=test["cb"],
        )
        frames = run_test(list(buf))
        print(f"         Sent {frames} frames")

    print("\n  Done.")
    print("  Observations to record:")
    print("    1. Did the white LED (LED2) respond at all?")
    print("    2. Does W PWM control white LED brightness?")
    print("    3. Does W current setting affect it?")
    print("    4. Does enabling W interfere with RGB reception?")
    print("    5. Do the parallel LEDs behave the same as series LED1?")


def cleanup():
    subprocess.run(["pinctrl", "set", "20", "op", "dl"], capture_output=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        cleanup()
