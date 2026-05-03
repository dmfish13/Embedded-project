#!/usr/bin/env python3
"""
PCB1 color selector with dimming, fade, strobe, and marquee chaser.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, C1=[0x20,0x20,0x20,0x20], 4 pixels.

Colors are WRGB tuples (W, R, G, B) matching TM1815B frame order.

Keys:
  Color keys  — select a solid color (stops any active mode)
  [6] Fade    — crossfade between random colors (1.5s fade, 0.5s hold)
  [7] Dimmer  — cycle brightness (disabled during Fade/Strobe)
  [8] Strobe  — switch to random color every 1.25s
  [9] Chaser  — colors chase through PCBs every 0.25s
  Ctrl+C      — quit

Random colors are chosen from the 16 eligible colors (15 RGB + Neutral
White, excluding Candlelight, Warm White, Cool White, Daylight).

Usage:
    python3 led_marquee_cycle_test.py
"""

import sys
import tty
import termios
import time
import random
import threading
from spidev import SpiDev

NUM_LEDS = 4

DIMMER_STEPS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

C1 = [0x20, 0x20, 0x20, 0x20]
C2 = [0xDF, 0xDF, 0xDF, 0xDF]
SPEED = 2_000_000
RESET = 80

FADE_DURATION = 1.5
FADE_HOLD = 0.5
FADE_STEP_INTERVAL = 0.03

STROBE_INTERVAL = 1.25
CHASER_INTERVAL = 0.35


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
    ('1', "Deep Red",              (0,   180, 0,   0)),
    ('2', "Mint",                  (0,   0,   225, 120)),
    ('3', "Dark Blue",             (0,   0,   0,   139)),
    ('4', "Red Prime",             (0,   255, 0,   0)),
    ('q', "Orange",                (0,   255, 100, 0)),
    ('w', "Light Blue",            (0,   100, 150, 255)),
    ('e', "Violet",                (0,   148, 0,   211)),
    ('r', "Green Prime",           (0,   0,   255, 0)),
    ('a', "Golden Rod",            (0,   218, 148, 0)),
    ('s', "Cyan",                  (0,   0,   255, 255)),
    ('d', "Purple",                (0,   128, 0,   128)),
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

ELIGIBLE_COLORS = [
    wrgb for _, name, wrgb in COLOR_MAP
    if wrgb is not None and name not in (
        "Candlelight ~1800K", "Warm White ~3000K",
        "Cool White ~5000K", "Daylight ~6500K")
]


def pick_random(exclude=None):
    """Pick a random eligible color different from exclude."""
    choices = [c for c in ELIGIBLE_COLORS if c != exclude]
    return random.choice(choices)


def lerp_pixel(a, b, t):
    """Linear interpolation between two WRGB tuples."""
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
        int(a[3] + (b[3] - a[3]) * t),
    )


def dim_pixel(wrgb, dimmer):
    return (
        int(wrgb[0] * dimmer),
        int(wrgb[1] * dimmer),
        int(wrgb[2] * dimmer),
        int(wrgb[3] * dimmer),
    )


def build_solid_buf(wrgb, dimmer):
    if wrgb is None:
        pixel = (0, 0, 0, 0)
    else:
        pixel = dim_pixel(wrgb, dimmer)
    return list(build_frame(C1, C2, [pixel] * NUM_LEDS, LUT_4BIT, reset_bytes=RESET))


def build_multi_buf(pcb_colors, dimmer):
    pixels = [dim_pixel(c, dimmer) for c in pcb_colors]
    return list(build_frame(C1, C2, pixels, LUT_4BIT, reset_bytes=RESET))


def main():
    print("=" * 68)
    print("  PCB1 Color Selector — Dimming / Fade / Strobe / Chaser")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (Section 5 baseline)")
    print("  C1=[0x20, 0x20, 0x20, 0x20]  C2=[0xDF, 0xDF, 0xDF, 0xDF]")
    print()
    print("  Color keys:")
    for key, name, wrgb in COLOR_MAP:
        if wrgb is None:
            print(f"    [{key}]  {name}")
        else:
            w, r, g, b = wrgb
            print(f"    [{key}]  {name:.<30s} W={w:>3} R={r:>3} G={g:>3} B={b:>3}")
    print()
    print("  Mode keys:")
    print(f"    [6]  Fade    — crossfade random colors "
          f"({FADE_DURATION}s fade, {FADE_HOLD}s hold)")
    print(f"    [7]  Dimmer  — cycle 1.0 → 0.9 → ... → 0.1 → 1.0 "
          f"(solid/chaser only)")
    print(f"    [8]  Strobe  — random color every {STROBE_INTERVAL}s")
    print(f"    [9]  Chaser  — colors chase through PCBs every "
          f"{CHASER_INTERVAL}s")
    print()
    print("  Ctrl+C to quit.")
    print("=" * 68)

    color_keys = {key for key, _, _ in COLOR_MAP}
    key_to_name = {key: name for key, name, _ in COLOR_MAP}
    key_to_wrgb = {key: wrgb for key, _, wrgb in COLOR_MAP}

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPEED
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    print(f"\n  SPI: requested {SPEED/1e6:.1f} MHz, actual {actual/1e6:.3f} MHz")

    dimmer_idx = 9
    current_color_key = 'p'
    current_wrgb = None
    active_mode = None  # None, 'fade', 'strobe', 'chaser'

    state = {'buf': build_solid_buf(None, 1.0), 'running': True}
    lock = threading.Lock()
    mode_stop = threading.Event()
    mode_thread = None

    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi.xfer2(buf[:])

    # --- Fade loop ---
    def fade_loop(start_color):
        current = start_color
        while not mode_stop.is_set():
            target = pick_random(exclude=current)
            steps = int(FADE_DURATION / FADE_STEP_INTERVAL)
            for i in range(steps + 1):
                if mode_stop.is_set():
                    return
                t = i / steps
                pixel = lerp_pixel(current, target, t)
                with lock:
                    state['buf'] = build_solid_buf(pixel, 1.0)
                time.sleep(FADE_STEP_INTERVAL)
            current = target
            if mode_stop.wait(FADE_HOLD):
                return

    # --- Strobe loop ---
    def strobe_loop(start_color):
        current = start_color
        with lock:
            state['buf'] = build_solid_buf(current, 1.0)
        while not mode_stop.wait(STROBE_INTERVAL):
            current = pick_random(exclude=current)
            with lock:
                state['buf'] = build_solid_buf(current, 1.0)

    # --- Chaser loop ---
    def chaser_loop():
        dim = DIMMER_STEPS[dimmer_idx]
        pcb_colors = [pick_random() for _ in range(NUM_LEDS)]
        with lock:
            state['buf'] = build_multi_buf(pcb_colors, dim)
        while not mode_stop.wait(CHASER_INTERVAL):
            dim = DIMMER_STEPS[dimmer_idx]
            for i in range(NUM_LEDS - 1, 0, -1):
                pcb_colors[i] = pcb_colors[i - 1]
            pcb_colors[0] = pick_random()
            with lock:
                state['buf'] = build_multi_buf(pcb_colors, dim)

    def stop_mode():
        nonlocal active_mode, mode_thread
        if mode_thread:
            mode_stop.set()
            mode_thread.join(timeout=2)
            mode_thread = None
        active_mode = None
        mode_stop.clear()

    def start_mode(mode, start_color=None):
        nonlocal active_mode, mode_thread
        stop_mode()
        active_mode = mode
        if mode == 'fade':
            sc = start_color if start_color else pick_random()
            mode_thread = threading.Thread(
                target=fade_loop, args=(sc,), daemon=True)
        elif mode == 'strobe':
            sc = start_color if start_color else pick_random()
            mode_thread = threading.Thread(
                target=strobe_loop, args=(sc,), daemon=True)
        elif mode == 'chaser':
            mode_thread = threading.Thread(
                target=chaser_loop, daemon=True)
        mode_thread.start()

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

            if ch == '6':
                sc = current_wrgb if (
                    active_mode is None and current_wrgb) else None
                start_mode('fade', sc)
                sys.stdout.write("  Fade: ON\r\n")
                sys.stdout.flush()

            elif ch == '7':
                if active_mode in ('fade', 'strobe'):
                    continue
                if dimmer_idx == 0:
                    dimmer_idx = 9
                else:
                    dimmer_idx -= 1
                dim = DIMMER_STEPS[dimmer_idx]
                if active_mode is None:
                    with lock:
                        state['buf'] = build_solid_buf(
                            current_wrgb, dim)
                sys.stdout.write(f"  Dimmer: {dim:.1f}\r\n")
                sys.stdout.flush()

            elif ch == '8':
                sc = current_wrgb if (
                    active_mode is None and current_wrgb) else None
                start_mode('strobe', sc)
                sys.stdout.write("  Strobe: ON\r\n")
                sys.stdout.flush()

            elif ch == '9':
                start_mode('chaser')
                sys.stdout.write("  Chaser: ON\r\n")
                sys.stdout.flush()

            elif ch in color_keys:
                stop_mode()
                current_color_key = ch
                current_wrgb = key_to_wrgb[ch]
                with lock:
                    state['buf'] = build_solid_buf(
                        current_wrgb, DIMMER_STEPS[dimmer_idx])
                sys.stdout.write(
                    f"  → {key_to_name[ch]} "
                    f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})\r\n")
                sys.stdout.flush()

    finally:
        mode_stop.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        state['running'] = False
        spi_thread.join(timeout=1)
        if mode_thread:
            mode_thread.join(timeout=1)
        spi.close()
        print("\n  Stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
