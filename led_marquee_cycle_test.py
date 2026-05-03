#!/usr/bin/env python3
"""
PCB1 color selector with multiple lighting modes.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, C1=[0x20,0x20,0x20,0x20], 4 pixels.

Colors are WRGB tuples (W, R, G, B) matching TM1815B frame order.

Keys:
  Color keys  — select a solid color (stops any active mode)
  [p] Power   — toggle LEDs on/off (saves/restores last setting)
  [y] Color 1 — next color key sets odd PCBs (D1, D3, D5)
  [u] Color 2 — next color key sets even PCBs (D2, D4, D6)
  [j] 2-hour  — auto power-off after 2 hours
  [k] 4-hour  — auto power-off after 4 hours
  [6] Fade    — crossfade between random colors (1.5s fade, 0.5s hold)
  [7] Dimmer  — cycle brightness (solid/dual/chaser only)
  [8] Strobe  — switch to random color every 1.25s
  [9] Chaser  — colors chase through PCBs every 0.25s
  [0] Theater — every-3rd-PCB chase in Neutral White (0.3s step)
  [-] Twinkle — independent fade-through-off color changes per PCB
  [=] Preset  — cycle through holiday color presets
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

NUM_LEDS = 6

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
THEATER_INTERVAL = 0.3

TWINKLE_FADE_DOWN = 1.25
TWINKLE_FADE_UP = 1.25
TWINKLE_HOLD = 0.5
TWINKLE_TICK = 0.03
TWINKLE_COLOR_COUNT = 200


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


OFF = (0, 0, 0, 0)
NEUTRAL_WHITE = (255, 0, 0, 0)

# (key, name, WRGB tuple)
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
]

# Power button — toggles LEDs on/off, saves/restores last setting
POWER = ('p', "Power Button")

ELIGIBLE_COLORS = [
    wrgb for _, name, wrgb in COLOR_MAP
    if wrgb is not None and name not in (
        "Candlelight ~1800K", "Warm White ~3000K",
        "Cool White ~5000K", "Daylight ~6500K")
]

FOREST_GREEN = (0, 10, 154, 24)
IRISH_GREEN = (0, 30, 196, 30)

WARM_WHITE = (154, 180, 77, 0)
DEEP_RED = (0, 150, 5, 5)
COOL_WHITE = (217, 0, 64, 128)
ORANGE = (0, 255, 100, 0)
RED_PRIME = (0, 255, 0, 0)
DARK_BLUE = (0, 0, 0, 139)

PRESET_COLOR_NAMES = {
    FOREST_GREEN: "Forest Green",
    IRISH_GREEN: "Irish Green",
    WARM_WHITE: "Warm White",
    DEEP_RED: "Deep Red",
    COOL_WHITE: "Cool White",
    ORANGE: "Orange",
    RED_PRIME: "Red Prime",
    DARK_BLUE: "Dark Blue",
}

PRESETS = [
    ("Christmas",       [WARM_WHITE, FOREST_GREEN, DEEP_RED]),
    ("St Patrick's Day", [IRISH_GREEN, COOL_WHITE, ORANGE]),
    ("4th of July",     [RED_PRIME, COOL_WHITE, DARK_BLUE]),
    ("Canada",          [DEEP_RED, COOL_WHITE, DEEP_RED]),
]


def pick_random(exclude=None):
    choices = [c for c in ELIGIBLE_COLORS if c != exclude]
    return random.choice(choices)


def lerp_pixel(a, b, t):
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
        pixel = OFF
    else:
        pixel = dim_pixel(wrgb, dimmer)
    return list(build_frame(C1, C2, [pixel] * NUM_LEDS, LUT_4BIT, reset_bytes=RESET))


def build_multi_buf(pcb_pixels):
    return list(build_frame(C1, C2, pcb_pixels, LUT_4BIT, reset_bytes=RESET))


def build_dual_buf(color1, color2, dimmer):
    pixels = []
    for n in range(NUM_LEDS):
        if n % 2 == 0:
            c = color1 if color1 else OFF
        else:
            c = color2 if color2 else OFF
        pixels.append(dim_pixel(c, dimmer))
    return list(build_frame(C1, C2, pixels, LUT_4BIT, reset_bytes=RESET))


def main():
    print("=" * 70)
    print("  PCB Color Selector — Solid / Fade / Strobe / Chaser / "
          "Theater / Twinkle / Preset")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4 → PCB5 → PCB6")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (Section 5 baseline)")
    print("  C1=[0x20, 0x20, 0x20, 0x20]  C2=[0xDF, 0xDF, 0xDF, 0xDF]")
    print()
    print("  Color keys:")
    for key, name, wrgb in COLOR_MAP:
        w, r, g, b = wrgb
        print(f"    [{key}]  {name:.<30s} W={w:>3} R={r:>3} G={g:>3} B={b:>3}")
    print()
    print(f"  Power:")
    print(f"    [{POWER[0]}]  {POWER[1]} — toggle on/off")
    print()
    print("  Dual-Color:")
    print(f"    [y]  Color 1  — next color key sets odd PCBs (D1, D3, D5)")
    print(f"    [u]  Color 2  — next color key sets even PCBs (D2, D4, D6)")
    print()
    print("  Timers:")
    print(f"    [j]  2-hour   — auto power-off after 2 hours")
    print(f"    [k]  4-hour   — auto power-off after 4 hours")
    print()
    print("  Mode keys:")
    print(f"    [6]  Fade     — crossfade random colors "
          f"({FADE_DURATION}s fade, {FADE_HOLD}s hold)")
    print(f"    [7]  Dimmer   — cycle 1.0 → 0.1 → 1.0 "
          f"(solid/dual/chaser only)")
    print(f"    [8]  Strobe   — random color every {STROBE_INTERVAL}s")
    print(f"    [9]  Chaser   — colors chase through PCBs every "
          f"{CHASER_INTERVAL}s")
    print(f"    [0]  Theater  — every-3rd-PCB chase, Neutral White "
          f"({THEATER_INTERVAL}s step)")
    print(f"    [-]  Twinkle  — independent fade-through-off per PCB")
    print(f"    [=]  Preset   — cycle holiday color presets")
    for name, pattern in PRESETS:
        colors = ", ".join(PRESET_COLOR_NAMES.get(c, "?") for c in pattern)
        print(f"           {name}: {colors}")
    print()
    print("  Ctrl+C to quit.")
    print("=" * 70)

    color_keys = {key for key, _, _ in COLOR_MAP}
    power_key = POWER[0]
    key_to_name = {key: name for key, name, _ in COLOR_MAP}
    key_to_wrgb = {key: wrgb for key, _, wrgb in COLOR_MAP}
    wrgb_to_name = {wrgb: name for _, name, wrgb in COLOR_MAP}

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPEED
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    print(f"\n  SPI: requested {SPEED/1e6:.1f} MHz, actual {actual/1e6:.3f} MHz")

    dimmer_idx = 9
    current_wrgb = None
    active_mode = None
    preset_idx = 0
    power_on = False
    saved_setting = None
    pending_slot = None
    dual_color1 = None
    dual_color2 = None
    timer_thread = None
    timer_cancel = threading.Event()
    timer_label = None

    state = {'buf': build_solid_buf(None, 1.0), 'running': True}
    lock = threading.Lock()
    mode_stop = threading.Event()
    mode_thread = None

    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi.xfer2(buf[:])

    # --- Fade ---
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

    # --- Strobe ---
    def strobe_loop(start_color):
        current = start_color
        with lock:
            state['buf'] = build_solid_buf(current, 1.0)
        while not mode_stop.wait(STROBE_INTERVAL):
            current = pick_random(exclude=current)
            with lock:
                state['buf'] = build_solid_buf(current, 1.0)

    # --- Chaser ---
    def chaser_loop():
        dim = DIMMER_STEPS[dimmer_idx]
        pcb_colors = [pick_random() for _ in range(NUM_LEDS)]
        with lock:
            state['buf'] = build_multi_buf(
                [dim_pixel(c, dim) for c in pcb_colors])
        while not mode_stop.wait(CHASER_INTERVAL):
            dim = DIMMER_STEPS[dimmer_idx]
            for i in range(NUM_LEDS - 1, 0, -1):
                pcb_colors[i] = pcb_colors[i - 1]
            pcb_colors[0] = pick_random()
            with lock:
                state['buf'] = build_multi_buf(
                    [dim_pixel(c, dim) for c in pcb_colors])

    # --- Theater Chase ---
    def theater_loop():
        offset = 0
        while not mode_stop.is_set():
            pixels = []
            for n in range(NUM_LEDS):
                if n % 3 == offset:
                    pixels.append(NEUTRAL_WHITE)
                else:
                    pixels.append(OFF)
            with lock:
                state['buf'] = build_multi_buf(pixels)
            if mode_stop.wait(THEATER_INTERVAL):
                return
            offset = (offset + 1) % 3

    # --- Twinkle ---
    def twinkle_loop():
        # Pre-generate all random values at init
        pcb_colors = []
        pcb_sequences = []
        pcb_delays = []
        for _ in range(NUM_LEDS):
            start_color = pick_random()
            pcb_colors.append(start_color)
            seq = [start_color]
            prev = start_color
            for _ in range(TWINKLE_COLOR_COUNT):
                nxt = pick_random(exclude=prev)
                seq.append(nxt)
                prev = nxt
            pcb_sequences.append(seq)
            pcb_delays.append(random.uniform(1.0, 4.0))

        # Phase tracking per PCB
        # Phases: 'delay', 'fade_down', 'fade_up', 'hold'
        pcb_phase = ['delay'] * NUM_LEDS
        pcb_phase_elapsed = [0.0] * NUM_LEDS
        pcb_seq_idx = [0] * NUM_LEDS
        pcb_current_pixel = list(pcb_colors)

        with lock:
            state['buf'] = build_multi_buf(pcb_current_pixel)

        while not mode_stop.is_set():
            time.sleep(TWINKLE_TICK)
            if mode_stop.is_set():
                return

            for p in range(NUM_LEDS):
                pcb_phase_elapsed[p] += TWINKLE_TICK
                phase = pcb_phase[p]
                elapsed = pcb_phase_elapsed[p]

                if phase == 'delay':
                    if elapsed >= pcb_delays[p]:
                        pcb_phase[p] = 'fade_down'
                        pcb_phase_elapsed[p] = 0.0

                elif phase == 'fade_down':
                    t = min(elapsed / TWINKLE_FADE_DOWN, 1.0)
                    src = pcb_sequences[p][pcb_seq_idx[p]]
                    pcb_current_pixel[p] = lerp_pixel(src, OFF, t)
                    if elapsed >= TWINKLE_FADE_DOWN:
                        pcb_seq_idx[p] += 1
                        if pcb_seq_idx[p] >= len(pcb_sequences[p]):
                            pcb_seq_idx[p] = 1
                        pcb_phase[p] = 'fade_up'
                        pcb_phase_elapsed[p] = 0.0

                elif phase == 'fade_up':
                    t = min(elapsed / TWINKLE_FADE_UP, 1.0)
                    dst = pcb_sequences[p][pcb_seq_idx[p]]
                    pcb_current_pixel[p] = lerp_pixel(OFF, dst, t)
                    if elapsed >= TWINKLE_FADE_UP:
                        pcb_current_pixel[p] = dst
                        pcb_phase[p] = 'hold'
                        pcb_phase_elapsed[p] = 0.0

                elif phase == 'hold':
                    if elapsed >= TWINKLE_HOLD:
                        pcb_phase[p] = 'fade_down'
                        pcb_phase_elapsed[p] = 0.0

            with lock:
                state['buf'] = build_multi_buf(pcb_current_pixel)

    # --- Preset ---
    def preset_loop(preset_idx):
        _, pattern = PRESETS[preset_idx]
        pixels = [pattern[n % len(pattern)] for n in range(NUM_LEDS)]
        with lock:
            state['buf'] = build_multi_buf(pixels)
        while not mode_stop.wait(0.5):
            pass

    def stop_mode():
        nonlocal active_mode, mode_thread
        if mode_thread:
            mode_stop.set()
            mode_thread.join(timeout=2)
            mode_thread = None
        active_mode = None
        mode_stop.clear()

    def start_mode(mode, start_color=None, preset_idx=0):
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
        elif mode == 'theater':
            mode_thread = threading.Thread(
                target=theater_loop, daemon=True)
        elif mode == 'twinkle':
            mode_thread = threading.Thread(
                target=twinkle_loop, daemon=True)
        elif mode == 'preset':
            mode_thread = threading.Thread(
                target=preset_loop, args=(preset_idx,), daemon=True)
        mode_thread.start()

    def cancel_timer():
        nonlocal timer_thread, timer_label
        if timer_thread:
            timer_cancel.set()
            timer_thread.join(timeout=1)
            timer_thread = None
            timer_label = None
        timer_cancel.clear()

    def timer_fire():
        nonlocal power_on, saved_setting, current_wrgb
        nonlocal timer_thread, timer_label
        if not power_on:
            timer_thread = None
            timer_label = None
            return
        saved_setting = {
            'wrgb': current_wrgb,
            'mode': active_mode,
            'dimmer_idx': dimmer_idx,
            'preset_idx': preset_idx,
            'dual_color1': dual_color1,
            'dual_color2': dual_color2,
        }
        stop_mode()
        current_wrgb = None
        with lock:
            state['buf'] = build_solid_buf(None, 1.0)
        power_on = False
        timer_thread = None
        timer_label = None
        sys.stdout.write("  Timer expired — Power: OFF\r\n")
        sys.stdout.flush()

    def start_timer(hours, label):
        nonlocal timer_thread, timer_label
        cancel_timer()
        timer_label = label
        def _wait():
            if not timer_cancel.wait(hours * 3600):
                timer_fire()
        timer_thread = threading.Thread(target=_wait, daemon=True)
        timer_thread.start()

    spi_thread = threading.Thread(target=spi_loop, daemon=True)
    spi_thread.start()

    print(f"  Power: OFF | Dimmer: {DIMMER_STEPS[dimmer_idx]:.1f}")
    print(f"  Press [{power_key}] to power on...\n")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == '\x03':
                break

            if ch == power_key:
                if power_on:
                    saved_setting = {
                        'wrgb': current_wrgb,
                        'mode': active_mode,
                        'dimmer_idx': dimmer_idx,
                        'preset_idx': preset_idx,
                        'dual_color1': dual_color1,
                        'dual_color2': dual_color2,
                    }
                    cancel_timer()
                    stop_mode()
                    current_wrgb = None
                    with lock:
                        state['buf'] = build_solid_buf(None, 1.0)
                    power_on = False
                    sys.stdout.write("  Power: OFF\r\n")
                    sys.stdout.flush()
                else:
                    power_on = True
                    if saved_setting:
                        dimmer_idx = saved_setting['dimmer_idx']
                        preset_idx = saved_setting['preset_idx']
                        dual_color1 = saved_setting['dual_color1']
                        dual_color2 = saved_setting['dual_color2']
                        mode = saved_setting['mode']
                        if mode == 'dual':
                            active_mode = 'dual'
                            with lock:
                                state['buf'] = build_dual_buf(
                                    dual_color1, dual_color2,
                                    DIMMER_STEPS[dimmer_idx])
                            sys.stdout.write(
                                f"  Power: ON (dual)\r\n")
                        elif mode:
                            if mode == 'preset':
                                start_mode('preset',
                                           preset_idx=preset_idx)
                            elif mode in ('fade', 'strobe'):
                                start_mode(mode,
                                           saved_setting['wrgb'])
                            else:
                                start_mode(mode)
                            sys.stdout.write(
                                f"  Power: ON ({mode})\r\n")
                        else:
                            current_wrgb = saved_setting['wrgb']
                            with lock:
                                state['buf'] = build_solid_buf(
                                    current_wrgb,
                                    DIMMER_STEPS[dimmer_idx])
                            sys.stdout.write(
                                f"  Power: ON "
                                f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})"
                                f"\r\n")
                    else:
                        current_wrgb = NEUTRAL_WHITE
                        with lock:
                            state['buf'] = build_solid_buf(
                                current_wrgb,
                                DIMMER_STEPS[dimmer_idx])
                        sys.stdout.write(
                            f"  Power: ON (Neutral White)\r\n")
                    sys.stdout.flush()

            elif ch in color_keys:
                power_on = True
                wrgb = key_to_wrgb[ch]
                if pending_slot:
                    slot = pending_slot
                    pending_slot = None
                    if slot == 'color1':
                        dual_color1 = wrgb
                    else:
                        dual_color2 = wrgb
                    stop_mode()
                    active_mode = 'dual'
                    dim = DIMMER_STEPS[dimmer_idx]
                    with lock:
                        state['buf'] = build_dual_buf(
                            dual_color1, dual_color2, dim)
                    c1n = wrgb_to_name.get(dual_color1, '—')
                    c2n = wrgb_to_name.get(dual_color2, '—')
                    sys.stdout.write(
                        f"  Dual: C1={c1n}, C2={c2n} "
                        f"(dim {dim:.1f})\r\n")
                else:
                    stop_mode()
                    dual_color1 = None
                    dual_color2 = None
                    current_wrgb = wrgb
                    with lock:
                        state['buf'] = build_solid_buf(
                            current_wrgb, DIMMER_STEPS[dimmer_idx])
                    sys.stdout.write(
                        f"  → {key_to_name[ch]} "
                        f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})\r\n")
                sys.stdout.flush()

            elif not power_on:
                continue

            elif ch == 'y':
                pending_slot = 'color1'
                sys.stdout.write(
                    "  Color 1: select a color for odd PCBs "
                    "(D1, D3, D5)...\r\n")
                sys.stdout.flush()

            elif ch == 'u':
                pending_slot = 'color2'
                sys.stdout.write(
                    "  Color 2: select a color for even PCBs "
                    "(D2, D4, D6)...\r\n")
                sys.stdout.flush()

            elif ch == 'j':
                start_timer(2, "2-hour")
                sys.stdout.write("  Timer: 2-hour\r\n")
                sys.stdout.flush()

            elif ch == 'k':
                start_timer(4, "4-hour")
                sys.stdout.write("  Timer: 4-hour\r\n")
                sys.stdout.flush()

            elif ch == '6':
                dimmer_idx = 9
                sc = current_wrgb if (
                    active_mode is None and current_wrgb) else None
                start_mode('fade', sc)
                sys.stdout.write("  Fade: ON\r\n")
                sys.stdout.flush()

            elif ch == '7':
                if active_mode in (
                        'fade', 'strobe', 'theater', 'twinkle',
                        'preset'):
                    continue
                if dimmer_idx == 0:
                    dimmer_idx = 9
                else:
                    dimmer_idx -= 1
                dim = DIMMER_STEPS[dimmer_idx]
                if active_mode == 'dual':
                    with lock:
                        state['buf'] = build_dual_buf(
                            dual_color1, dual_color2, dim)
                elif active_mode is None:
                    with lock:
                        state['buf'] = build_solid_buf(
                            current_wrgb, dim)
                sys.stdout.write(f"  Dimmer: {dim:.1f}\r\n")
                sys.stdout.flush()

            elif ch == '8':
                dimmer_idx = 9
                sc = current_wrgb if (
                    active_mode is None and current_wrgb) else None
                start_mode('strobe', sc)
                sys.stdout.write("  Strobe: ON\r\n")
                sys.stdout.flush()

            elif ch == '9':
                dimmer_idx = 9
                start_mode('chaser')
                sys.stdout.write("  Chaser: ON\r\n")
                sys.stdout.flush()

            elif ch == '0':
                dimmer_idx = 9
                start_mode('theater')
                sys.stdout.write("  Theater Chase: ON\r\n")
                sys.stdout.flush()

            elif ch == '-':
                dimmer_idx = 9
                start_mode('twinkle')
                sys.stdout.write("  Twinkle: ON\r\n")
                sys.stdout.flush()

            elif ch == '=':
                dimmer_idx = 9
                if active_mode == 'preset':
                    preset_idx = (preset_idx + 1) % len(PRESETS)
                else:
                    preset_idx = 0
                start_mode('preset', preset_idx=preset_idx)
                name = PRESETS[preset_idx][0]
                sys.stdout.write(f"  Preset: {name}\r\n")
                sys.stdout.flush()

    finally:
        cancel_timer()
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
