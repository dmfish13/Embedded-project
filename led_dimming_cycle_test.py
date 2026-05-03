#!/usr/bin/env python3
"""
PCB1 color selector with dimming — 20 colors + off, selected by keyboard.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, C1=[0x1E,0x1E,0x1E,0x1E], 4 pixels.

Colors are WRGB tuples (W, R, G, B) matching TM1815B frame order.
All four D1 PWM values are multiplied by the dimmer before output.

Press a color key to switch colors. Press [7] to cycle the dimmer
(1.0 → 0.9 → ... → 0.1 → 1.0). Ctrl+C to quit.

Usage:
    python3 led_dimming_cycle_test.py
"""

import sys
import tty
import termios
import threading
from spidev import SpiDev

NUM_LEDS = 4

DIMMER_STEPS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


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


# (key, name, WRGB tuple) — Off uses None
COLOR_MAP = [
    ('1', "Deep Red",              (0,   150, 5,   5)),
    ('2', "Mint",                  (0,   0,   225, 120)),
    ('3', "Dark Blue",             (0,   0,   0,   139)),
    ('4', "Red Prime",             (0,   255, 0,   0)),
    ('q', "Orange",                (0,   255, 100, 0)),
    ('w', "Light Blue",            (0,   100, 150, 255)),
    ('e', "Violet",                (0,   100, 0,   211)),
    ('r', "Green Prime",           (0,   0,   255, 0)),
    ('a', "Golden Rod",            (0,   218, 148, 0)),
    ('s', "Cyan",                  (0,   0,   255, 255)),
    ('d', "Purple",                (0,   128, 0,   168)),
    ('f', "Blue Prime",            (0,   0,   0,   255)),
    ('z', "Yellow",                (0,   255, 230, 0)),
    ('x', "Steel Blue",            (0,   70,  130, 180)),
    ('c', "Magenta",               (0,   255, 0,   255)),
    ('v', "Candlelight ~1800K",    (76,  255, 128, 0)),
    ('b', "Warm White ~3000K",     (154, 180, 77,  0)),
    ('n', "Neutral White ~4000K",  (255, 0,   0,   0)),
    ('m', "Cool White ~5000K",     (217, 0,   64,  128)),
    (',', "Daylight ~6500K",       (178, 0,   128, 255)),
    ('p', "Off",                   None),
]

C1 = [0x1E, 0x1E, 0x1E, 0x1E]
C2 = [0xE1, 0xE1, 0xE1, 0xE1]
SPEED = 2_000_000
RESET = 80


def build_all_bufs():
    """Pre-build frame buffers for every color × dimmer combination."""
    bufs = {}
    for key, name, wrgb in COLOR_MAP:
        for di, dim in enumerate(DIMMER_STEPS):
            if wrgb is None:
                pixel = (0, 0, 0, 0)
            else:
                pixel = (
                    int(wrgb[0] * dim),
                    int(wrgb[1] * dim),
                    int(wrgb[2] * dim),
                    int(wrgb[3] * dim),
                )
            buf = build_frame(C1, C2, [pixel] * NUM_LEDS, LUT_4BIT, reset_bytes=RESET)
            bufs[(key, di)] = list(buf)
    return bufs


def main():
    print("=" * 68)
    print("  PCB1 Color Selector with Dimming")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (Section 5 baseline)")
    print("  C1=[0x1E, 0x1E, 0x1E, 0x1E]  C2=[0xE1, 0xE1, 0xE1, 0xE1]")
    print()
    print("  Key assignments:")
    for key, name, wrgb in COLOR_MAP:
        if wrgb is None:
            print(f"    [{key}]  {name}")
        else:
            w, r, g, b = wrgb
            print(f"    [{key}]  {name:.<30s} W={w:>3} R={r:>3} G={g:>3} B={b:>3}")
    print()
    print(f"    [7]  Dimmer (cycles 1.0 → 0.9 → ... → 0.1 → 1.0)")
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
    color_keys = {key for key, _, _ in COLOR_MAP}
    key_to_name = {key: name for key, name, _ in COLOR_MAP}

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPEED
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    print(f"\n  SPI: requested {SPEED/1e6:.1f} MHz, actual {actual/1e6:.3f} MHz")

    dimmer_idx = 9  # 1.0
    current_color = 'p'

    state = {'buf': bufs[('p', dimmer_idx)], 'running': True}
    lock = threading.Lock()

    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi.xfer2(buf[:])

    spi_thread = threading.Thread(target=spi_loop, daemon=True)
    spi_thread.start()

    print(f"  Active: Off | Dimmer: {DIMMER_STEPS[dimmer_idx]:.1f}")
    print(f"  Press a key to select a color...\n")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x03':
                break
            if ch == '7':
                if dimmer_idx == 0:
                    dimmer_idx = 9
                else:
                    dimmer_idx -= 1
                with lock:
                    state['buf'] = bufs[(current_color, dimmer_idx)]
                sys.stdout.write(
                    f"  Dimmer: {DIMMER_STEPS[dimmer_idx]:.1f}\r\n")
                sys.stdout.flush()
            elif ch in color_keys:
                current_color = ch
                with lock:
                    state['buf'] = bufs[(current_color, dimmer_idx)]
                sys.stdout.write(
                    f"  → {key_to_name[ch]} "
                    f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})\r\n")
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
