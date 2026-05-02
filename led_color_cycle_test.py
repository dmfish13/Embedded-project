#!/usr/bin/env python3
"""
PCB1 color cycle test — 16 colors from button_map.py.

Uses the same frame format as test 12 (the only test that doesn't
freeze the LEDs): C1=[0x00,0x00,0x00,0x00], C2=[0xFF,0xFF,0xFF,0xFF],
4 pixels per frame, 16-bit encoding at 4.0 MHz.

Colors from button_map.py are RGBW tuples (R,G,B,W).
TM1815B frame order is WRGB, so we reorder before sending.
"""

import math
import sys
import threading
from spidev import SpiDev

RESET_TARGET_US = 250
SPI_SPEED = 4_000_000

NUM_LEDS = 4
C1 = [0x00, 0x00, 0x00, 0x00]
C2 = [0xFF, 0xFF, 0xFF, 0xFF]


def reset_bytes_for_speed(spi_speed):
    return max(math.ceil(RESET_TARGET_US * spi_speed / 8_000_000), 50)


def encode_byte_16bit(value):
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


LUT = [encode_byte_16bit(v) for v in range(256)]


def build_frame(pixels):
    """Frame: [Reset][C1][C2][D1][D2][D3][D4][Reset] — same as test 12."""
    reset = reset_bytes_for_speed(SPI_SPEED)
    buf = bytearray(b'\xFF' * reset)
    for bv in C1:
        buf += LUT[bv]
    for bv in C2:
        buf += LUT[bv]
    for pixel in pixels:
        for ch in pixel:
            buf += LUT[ch]
    buf += b'\xFF' * reset
    return buf


def run_test(buf_list):
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0
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


def rgbw_to_wrgb(r, g, b, w):
    return (w, r, g, b)


COLORS = [
    ("Deep Red",    (180, 0,   0,   0)),
    ("Mint",        (0,   200, 120, 0)),
    ("Dark Blue",   (0,   0,   139, 0)),
    ("Red Prime",   (255, 0,   0,   0)),
    ("Orange",      (255, 100, 0,   0)),
    ("Light Blue",  (100, 150, 255, 0)),
    ("Violet",      (148, 0,   211, 0)),
    ("Green Prime", (0,   255, 0,   0)),
    ("Yellow",      (255, 255, 0,   0)),
    ("Cyan",        (0,   255, 255, 0)),
    ("Purple",      (128, 0,   128, 0)),
    ("Blue Prime",  (0,   0,   255, 0)),
    ("Neon Yellow", (220, 255, 0,   0)),
    ("Steel Blue",  (70,  130, 180, 0)),
    ("Magenta",     (255, 0,   255, 0)),
    ("White",       (0,   0,   0,   255)),
]


def main():
    print("=" * 60)
    print("  PCB1 Color Cycle — 16 colors from button_map.py")
    print("  4 pixels per frame, 16-bit @ 4.0 MHz")
    print("  C1=[0x00,0x00,0x00,0x00] C2=[0xFF,0xFF,0xFF,0xFF]")
    print("  (Same format as test 12 which doesn't freeze)")
    print()
    print("  All 4 PCBs get the same color each test.")
    print("  Does PCB1 change color for each test?")
    print()
    print("  Press Enter to advance through each color.")
    print("=" * 60)

    for i, (name, rgbw) in enumerate(COLORS, 1):
        r, g, b, w = rgbw
        wrgb = rgbw_to_wrgb(r, g, b, w)
        pixels = [wrgb] * NUM_LEDS

        print(f"\n  [{i:>2}/16] {name}")
        print(f"         RGBW: R={r:>3} G={g:>3} B={b:>3} W={w:>3}")
        print(f"         Frame WRGB: W={wrgb[0]:>3} R={wrgb[1]:>3} "
              f"G={wrgb[2]:>3} B={wrgb[3]:>3}")
        print(f"         C1={C1}  C2={C2}  Pixels: 4x same")

        frame = build_frame(pixels)
        input("         Press Enter to start...")
        frames = run_test(list(frame))
        print(f"         Sent {frames} frames. What color did PCB1 show?")

    print("\n  Done. Did PCB1 change color for each test?")
    print("  If yes: chip responds to different data correctly.")
    print("  If no (stuck on one color): chip not accepting new frames.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
