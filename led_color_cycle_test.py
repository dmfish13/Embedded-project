#!/usr/bin/env python3
"""
PCB1 color selector — 20 colors + off, selected by keyboard.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, C1=[0x1E,0x1E,0x1E,0x1E], 4 pixels.

Colors from button_map.py are RGBW tuples (R,G,B,W).
TM1815B frame order is WRGB, so we reorder before sending.

Press the assigned key to instantly switch colors. Ctrl+C to quit.

Usage:
    python3 led_color_cycle_test.py
"""

import sys
import tty
import termios
import threading
from spidev import SpiDev

NUM_LEDS = 4


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


def rgbw_to_wrgb(r, g, b, w):
    return (w, r, g, b)


# (key, name, RGBW tuple) — Off uses None for RGBW
COLOR_MAP = [
    ('1', "Deep Red",              (180, 0,   0,   0)),
    ('2', "Mint",                  (0,   200, 120, 0)),
    ('3', "Dark Blue",             (0,   0,   139, 0)),
    ('4', "Red Prime",             (255, 0,   0,   0)),
    ('q', "Orange",                (255, 100, 0,   0)),
    ('w', "Light Blue",            (100, 150, 255, 0)),
    ('e', "Violet",                (148, 0,   211, 0)),
    ('r', "Green Prime",           (0,   255, 0,   0)),
    ('a', "Yellow",                (255, 255, 0,   0)),
    ('s', "Cyan",                  (0,   255, 255, 0)),
    ('d', "Purple",                (128, 0,   128, 0)),
    ('f', "Blue Prime",            (0,   0,   255, 0)),
    ('z', "Neon Yellow",           (220, 255, 0,   0)),
    ('x', "Steel Blue",            (70,  130, 180, 0)),
    ('c', "Magenta",               (255, 0,   255, 0)),
    ('v', "Candlelight ~1800K",    (255, 128, 0,   76)),
    ('b', "Warm White ~3000K",     (255, 128, 12,  255)),
    ('n', "Neutral White ~4000K",  (0,   0,   0,   255)),
    ('m', "Cool White ~5000K",     (0,   64,  128, 217)),
    (',', "Daylight ~6500K",       (0,   128, 255, 178)),
    ('p', "Off",                   None),
]

C1 = [0x1E, 0x1E, 0x1E, 0x1E]
C2 = [0xE1, 0xE1, 0xE1, 0xE1]
SPEED = 2_000_000
RESET = 80


def build_all_bufs():
    """Pre-build frame buffers for every color. Off = all zeros."""
    bufs = {}
    for key, name, rgbw in COLOR_MAP:
        if rgbw is None:
            wrgb = (0, 0, 0, 0)
        else:
            r, g, b, w = rgbw
            wrgb = rgbw_to_wrgb(r, g, b, w)
        buf = build_frame(C1, C2, [wrgb] * NUM_LEDS, LUT_4BIT, reset_bytes=RESET)
        bufs[key] = list(buf)
    return bufs


def main():
    print("=" * 68)
    print("  PCB1 Color Selector — press a key to switch color")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (Section 5 baseline)")
    print("  C1=[0x1E, 0x1E, 0x1E, 0x1E]  C2=[0xE1, 0xE1, 0xE1, 0xE1]")
    print()
    print("  Key assignments:")
    for key, name, rgbw in COLOR_MAP:
        if rgbw is None:
            print(f"    [{key}]  {name}")
        else:
            r, g, b, w = rgbw
            print(f"    [{key}]  {name:.<30s} R={r:>3} G={g:>3} B={b:>3} W={w:>3}")
    print()
    print("  Ctrl+C to quit.")
    print("=" * 68)

    for j in range(4):
        if C2[j] != (C1[j] ^ 0xFF):
            print(f"  *** ERROR: C2[{j}]=0x{C2[j]:02X} is NOT "
                  f"~C1[{j}]=0x{C1[j]:02X} "
                  f"(expected 0x{C1[j] ^ 0xFF:02X}) ***")
            sys.exit(1)

    bufs = build_all_bufs()
    valid_keys = {key for key, _, _ in COLOR_MAP}
    key_to_name = {key: name for key, name, _ in COLOR_MAP}

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPEED
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    print(f"\n  SPI: requested {SPEED/1e6:.1f} MHz, actual {actual/1e6:.3f} MHz")

    # Mutable container so the SPI thread always sees updates
    state = {'buf': bufs['p'], 'running': True}
    lock = threading.Lock()

    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi.xfer2(buf[:])

    spi_thread = threading.Thread(target=spi_loop, daemon=True)
    spi_thread.start()

    print(f"  Active: Off")
    print(f"  Press a key to select a color...\n")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x03':
                break
            if ch in valid_keys:
                with lock:
                    state['buf'] = bufs[ch]
                # Raw mode needs \r\n for proper line breaks
                sys.stdout.write(f"  → {key_to_name[ch]}\r\n")
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        state['running'] = False
        spi_thread.join(timeout=1)
        spi.close()
        print("\n  Stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
