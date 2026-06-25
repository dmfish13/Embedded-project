#!/usr/bin/env python3
"""
PCB1 color selector — 20 colors + off, selected by keyboard.
Fixed: Uses buffer copying to allow color re-selection.

Usage:
    python3 led_color_cycle_test.py
"""

import sys
import tty
import termios
import threading
import time
from spidev import SpiDev

NUM_LEDS = 4
C1 = [0x1E, 0x1E, 0x1E, 0x1E]
C2 = [0xE1, 0xE1, 0xE1, 0xE1]
SPEED = 2_000_000
RESET = 80

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

# Pre-compute the 4-bit lookup table
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

def build_all_bufs():
    """Pre-build frame buffers for every color."""
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
    print(f"  System: Raspberry Pi 5 SPI Mode")
    print("=" * 68)

    # Validate C1/C2 relationship
    for j in range(4):
        if C2[j] != (C1[j] ^ 0xFF):
            print(f"*** ERROR: C2 inverse mismatch at index {j} ***")
            sys.exit(1)

    bufs = build_all_bufs()
    valid_keys = {key for key, _, _ in COLOR_MAP}
    key_to_name = {key: name for key, name, _ in COLOR_MAP}

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPEED
    spi.mode = 0b