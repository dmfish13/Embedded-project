#!/usr/bin/env python3
"""
Enbrighten LED Cafe Lights — RF Remote Controller

Raspberry Pi 5 dual-bus controller:
  SPI0 → nRF24L01+ radio (BK2423/XN297, channel 42, 1 Mbps)
  SPI1 → TM1815B RGBW LEDs (4-bit encoding @ 2.0 MHz)

Startup sequence:
  1. Configures nRF24L01+ with hardware address filtering (non-promiscuous)
  2. Waits for the remote to send a packet — confirms pairing
  3. Receives button presses and drives LEDs accordingly

25 buttons:
  Power, Fade, Dimming, Strobe, Color1, 2-hour, Color2, 4-hour,
  Modes (cycles: Christmas, St Patrick's, 4th of July, Canada,
         Chaser, Theater Chase, Twinkle),
  15 color buttons, White (cycles temperatures)

Run with --learn to capture button hex codes from the remote.

Usage:
    python3 main.py
    python3 main.py --learn
"""

import os
import sys
import json
import time
import random
import threading
import board
import busio
import digitalio
from spidev import SpiDev


# ═══════════════════════════════════════════════════════════════════════
# LED Constants
# ═══════════════════════════════════════════════════════════════════════

NUM_LEDS = 6

C1 = [0x20, 0x20, 0x20, 0x20]
C2 = [0xDF, 0xDF, 0xDF, 0xDF]
LED_SPI_SPEED = 2_000_000
RESET = 80

DIMMER_STEPS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

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


# ═══════════════════════════════════════════════════════════════════════
# RF Constants (BK2423 / XN297 protocol)
# ═══════════════════════════════════════════════════════════════════════

BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])

SCRAMBLE_B = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
]

NRF24_ADDR = [0x38, 0x72, 0x2D, 0xA8, 0x5E]
ADDR_WIDTH = 5
RF_CHANNEL = 42

COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])
IDLE_HEADER = bytes([0x4C, 0xED, 0xAD, 0x0B])

DEBOUNCE_S = 0.30


# ═══════════════════════════════════════════════════════════════════════
# Color Definitions — WRGB (White, Red, Green, Blue)
# ═══════════════════════════════════════════════════════════════════════

OFF           = (0,   0,   0,   0)
NEUTRAL_WHITE = (255, 0,   0,   0)
DEEP_RED      = (0,   150, 5,   5)
MINT          = (0,   0,   225, 120)
DARK_BLUE     = (0,   0,   0,   139)
RED_PRIME     = (0,   255, 0,   0)
ORANGE        = (0,   255, 100, 0)
LIGHT_BLUE    = (0,   100, 150, 255)
VIOLET        = (0,   100, 0,   211)
GREEN_PRIME   = (0,   0,   255, 0)
GOLDEN_ROD    = (0,   218, 148, 0)
CYAN          = (0,   0,   255, 255)
PURPLE        = (0,   128, 0,   168)
BLUE_PRIME    = (0,   0,   0,   255)
YELLOW        = (0,   255, 230, 0)
STEEL_BLUE    = (0,   70,  130, 180)
MAGENTA       = (0,   255, 0,   255)
CANDLELIGHT   = (76,  255, 128, 0)
WARM_WHITE    = (154, 180, 77,  0)
COOL_WHITE    = (217, 0,   64,  128)
DAYLIGHT      = (178, 0,   128, 255)
FOREST_GREEN  = (0,   10,  154, 24)
IRISH_GREEN   = (0,   30,  196, 30)

WHITE_TEMPS = [
    ("Candlelight ~1800K",   CANDLELIGHT),
    ("Warm White ~3000K",    WARM_WHITE),
    ("Neutral White ~4000K", NEUTRAL_WHITE),
    ("Cool White ~5000K",    COOL_WHITE),
    ("Daylight ~6500K",      DAYLIGHT),
]

COLOR_BUTTONS = {
    "Deep_Red":    DEEP_RED,
    "Mint":        MINT,
    "Dark_Blue":   DARK_BLUE,
    "Red_Prime":   RED_PRIME,
    "Orange":      ORANGE,
    "Light_Blue":  LIGHT_BLUE,
    "Violet":      VIOLET,
    "Green_Prime": GREEN_PRIME,
    "Golden_Rod":  GOLDEN_ROD,
    "Cyan":        CYAN,
    "Purple":      PURPLE,
    "Blue_Prime":  BLUE_PRIME,
    "Yellow":      YELLOW,
    "Steel_Blue":  STEEL_BLUE,
    "Magenta":     MAGENTA,
}

ELIGIBLE_COLORS = [
    DEEP_RED, MINT, DARK_BLUE, RED_PRIME, ORANGE, LIGHT_BLUE,
    VIOLET, GREEN_PRIME, GOLDEN_ROD, CYAN, PURPLE, BLUE_PRIME,
    YELLOW, STEEL_BLUE, MAGENTA, NEUTRAL_WHITE,
]

COLOR_NAMES = {
    DEEP_RED: "Deep Red", MINT: "Mint", DARK_BLUE: "Dark Blue",
    RED_PRIME: "Red Prime", ORANGE: "Orange", LIGHT_BLUE: "Light Blue",
    VIOLET: "Violet", GREEN_PRIME: "Green Prime", GOLDEN_ROD: "Golden Rod",
    CYAN: "Cyan", PURPLE: "Purple", BLUE_PRIME: "Blue Prime",
    YELLOW: "Yellow", STEEL_BLUE: "Steel Blue", MAGENTA: "Magenta",
    NEUTRAL_WHITE: "Neutral White", CANDLELIGHT: "Candlelight",
    WARM_WHITE: "Warm White", COOL_WHITE: "Cool White", DAYLIGHT: "Daylight",
    FOREST_GREEN: "Forest Green", IRISH_GREEN: "Irish Green",
}


# ═══════════════════════════════════════════════════════════════════════
# Modes list — cycled by the Modes button
# (name, mode_type, pattern_or_None)
# ═══════════════════════════════════════════════════════════════════════

MODES_LIST = [
    ("Christmas",        "preset",  [NEUTRAL_WHITE, FOREST_GREEN, DEEP_RED]),
    ("St Patrick's Day", "preset",  [IRISH_GREEN, COOL_WHITE, ORANGE]),
    ("4th of July",      "preset",  [RED_PRIME, COOL_WHITE, DARK_BLUE]),
    ("Canada",           "preset",  [DEEP_RED, COOL_WHITE, DEEP_RED]),
    ("Chaser",           "chaser",  None),
    ("Theater Chase",    "theater", None),
    ("Twinkle",          "twinkle", None),
]

BUTTON_NAMES = [
    "Power", "Fade", "Dimming", "Strobe",
    "Color1", "2-hour", "Color2", "4-hour",
    "Modes",
    "Deep_Red", "Mint", "Dark_Blue",
    "Red_Prime", "Orange", "Light_Blue", "Violet",
    "Green_Prime", "Golden_Rod", "Cyan", "Purple",
    "Blue_Prime", "Yellow", "Steel_Blue", "Magenta",
    "White_Select",
]

BUTTON_CODE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "button_codes.json")


# ═══════════════════════════════════════════════════════════════════════
# TM1815B LED Encoding (4-bit protocol @ 2.0 MHz)
# ═══════════════════════════════════════════════════════════════════════

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
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf


def dim_pixel(wrgb, dimmer):
    return (
        int(wrgb[0] * dimmer), int(wrgb[1] * dimmer),
        int(wrgb[2] * dimmer), int(wrgb[3] * dimmer),
    )


def lerp_pixel(a, b, t):
    return (
        int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t), int(a[3] + (b[3] - a[3]) * t),
    )


def pick_random(exclude=None):
    choices = [c for c in ELIGIBLE_COLORS if c != exclude]
    return random.choice(choices)


def build_solid_buf(wrgb, dimmer):
    pixel = dim_pixel(wrgb, dimmer) if wrgb else OFF
    return list(build_frame(C1, C2, [pixel] * NUM_LEDS, LUT_4BIT,
                            reset_bytes=RESET))


def build_multi_buf(pcb_pixels):
    return list(build_frame(C1, C2, pcb_pixels, LUT_4BIT,
                            reset_bytes=RESET))


def build_dual_buf(color1, color2, dimmer):
    pixels = []
    for n in range(NUM_LEDS):
        c = (color1 if color1 else OFF) if n % 2 == 0 \
            else (color2 if color2 else OFF)
        pixels.append(dim_pixel(c, dimmer))
    return list(build_frame(C1, C2, pixels, LUT_4BIT, reset_bytes=RESET))


# ═══════════════════════════════════════════════════════════════════════
# nRF24L01+ Driver (raw SPI register access via Blinka)
# ═══════════════════════════════════════════════════════════════════════

class NRF24L01:
    def __init__(self, spi, csn, ce):
        self._spi = spi
        self._csn = csn
        self._ce = ce
        self._csn.direction = digitalio.Direction.OUTPUT
        self._csn.value = True
        self._ce.direction = digitalio.Direction.OUTPUT
        self._ce.value = False
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000, polarity=0, phase=0)
        self._spi.unlock()

    def _reg_read(self, reg):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        buf = bytearray([reg, 0xFF])
        self._spi.write_readinto(buf, buf)
        self._csn.value = True
        self._spi.unlock()
        return buf[1]

    def _reg_write(self, reg, value):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg, value]))
        self._csn.value = True
        self._spi.unlock()

    def _reg_write_bytes(self, reg, data):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg]) + bytearray(data))
        self._csn.value = True
        self._spi.unlock()

    def _cmd(self, cmd):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([cmd]))
        self._csn.value = True
        self._spi.unlock()

    def _read_payload(self, length):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        tx = bytearray([0x61] + [0xFF] * length)
        rx = bytearray(len(tx))
        self._spi.write_readinto(tx, rx)
        self._csn.value = True
        self._spi.unlock()
        return rx[1:]

    def configure(self):
        """Configure for BK2423/XN297 reception on channel 42.

        Address filtering on pipe 0 only — non-promiscuous.
        Only packets matching NRF24_ADDR are received.
        """
        self._ce.value = False
        self._reg_write(0x00, 0x03)          # PWR_UP | PRIM_RX
        time.sleep(0.002)
        self._reg_write(0x01, 0x00)          # no auto-ack
        self._reg_write(0x02, 0x01)          # enable pipe 0 only
        self._reg_write(0x03, ADDR_WIDTH - 2)  # 5-byte address
        self._reg_write_bytes(0x0A, bytes(NRF24_ADDR))  # RX addr pipe 0
        self._reg_write(0x11, 32)            # 32-byte payload
        self._reg_write(0x06, 0x07)          # 1 Mbps, max power
        self._reg_write(0x1C, 0x00)          # no dynamic payload
        self._reg_write(0x1D, 0x00)          # features off
        self._cmd(0xE2)                      # flush RX FIFO
        self._cmd(0xE1)                      # flush TX FIFO
        self._reg_write(0x07, 0x70)          # clear status flags
        self._reg_write(0x05, RF_CHANNEL)    # channel 42
        self._ce.value = True                # start listening

    def available(self):
        fifo = self._reg_read(0x17)
        return not bool(fifo & 0x01)

    def read(self):
        data = self._read_payload(32)
        self._reg_write(0x07, 0x70)
        return data

    def power_down(self):
        self._ce.value = False
        self._reg_write(0x00, 0x00)


# ═══════════════════════════════════════════════════════════════════════
# RF Descramble (BK2423 Scramble Table B + bit reversal)
# ═══════════════════════════════════════════════════════════════════════

def descramble(raw):
    result = bytearray(len(raw))
    for i in range(len(raw)):
        b = BIT_REVERSE[raw[i]]
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            result[i] = b ^ SCRAMBLE_B[idx]
        else:
            result[i] = b
    return bytes(result)


def to_raw(desc_bytes):
    raw = []
    for i in range(len(desc_bytes)):
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            bit_rev = desc_bytes[i] ^ SCRAMBLE_B[idx]
        else:
            bit_rev = desc_bytes[i]
        raw.append(BIT_REVERSE[bit_rev])
    return bytes(raw)


def to_hex(data):
    return "".join(f"{b:02X}" for b in data)


def find_idle_start(desc_bytes):
    raw = to_raw(desc_bytes)
    if len(raw) < 5:
        return len(raw)
    tail_val = raw[-1]
    if tail_val not in (0xFF, 0x55, 0xAA):
        return len(raw)
    start = len(raw)
    for i in range(len(raw) - 2, -1, -1):
        if raw[i] == tail_val or bin(raw[i] ^ tail_val).count('1') <= 1:
            start = i
        else:
            break
    return start


def is_background_packet(desc):
    return desc[:4] == COMMAND_HEADER or desc[:4] == IDLE_HEADER


def extract_button_code(desc):
    idle_at = find_idle_start(desc)
    return to_hex(desc[:idle_at])


# ═══════════════════════════════════════════════════════════════════════
# Button Code Storage (button_codes.json)
# ═══════════════════════════════════════════════════════════════════════

def load_button_codes():
    if os.path.exists(BUTTON_CODE_FILE):
        try:
            with open(BUTTON_CODE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_button_codes(codes):
    with open(BUTTON_CODE_FILE, 'w') as f:
        json.dump(codes, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════
# Pairing — confirm the remote is reachable
# ═══════════════════════════════════════════════════════════════════════

def pair_remote(radio):
    """Wait for a valid remote packet to confirm the remote is present.

    The nRF24L01+ address filter on pipe 0 ensures only packets from
    the remote's address are received — this IS the lock mechanism.
    No other device on a different address can be heard.
    """
    print("\n  Scanning for remote...")
    print(f"  Channel {RF_CHANNEL} ({2400 + RF_CHANNEL} MHz) | "
          f"Address [{' '.join(f'{b:02X}' for b in NRF24_ADDR)}]")
    print("  Press any button on the remote to pair...\n")

    radio.configure()
    start = time.monotonic()
    bg_count = 0
    last_status = start

    while True:
        if radio.available():
            raw = radio.read()
            desc = descramble(raw)

            if is_background_packet(desc):
                bg_count += 1
                continue

            code = extract_button_code(desc)
            elapsed = time.monotonic() - start

            confirm_deadline = time.monotonic() + 5.0
            while time.monotonic() < confirm_deadline:
                if radio.available():
                    raw2 = radio.read()
                    desc2 = descramble(raw2)
                    if not is_background_packet(desc2):
                        print(f"  Remote paired! ({elapsed:.1f}s, "
                              f"{bg_count} background packets filtered)")
                        return True

            print("  Got one packet but couldn't confirm. "
                  "Press a button again...")
            start = time.monotonic()
            bg_count = 0

        now = time.monotonic()
        if now - last_status >= 10.0:
            elapsed = now - start
            print(f"    Still scanning... "
                  f"({elapsed:.0f}s, {bg_count} background filtered)")
            last_status = now

        time.sleep(0.001)


# ═══════════════════════════════════════════════════════════════════════
# Learning Mode — capture hex codes for each button
# ═══════════════════════════════════════════════════════════════════════

def learn_buttons(radio):
    """Guide the user through pressing each button to capture its hex code."""
    print("\n" + "=" * 70)
    print("  BUTTON LEARNING MODE")
    print("  Press each button when prompted. Hold for 2 seconds.")
    print("=" * 70)

    codes = load_button_codes()

    for btn_name in BUTTON_NAMES:
        if btn_name in codes:
            print(f"\n  [{btn_name}] already learned: "
                  f"{codes[btn_name][:24]}...")
            resp = input("  Re-learn? (y/N): ").strip().lower()
            if resp != 'y':
                continue

        input(f"\n  Press and HOLD [{btn_name}], then press Enter...")
        print("  Capturing (hold for 2 seconds)...")

        captured = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if radio.available():
                raw = radio.read()
                desc = descramble(raw)
                if not is_background_packet(desc):
                    captured.append(extract_button_code(desc))

        if not captured:
            print(f"  No remote packets for [{btn_name}]. Skipping.")
            continue

        code_counts = {}
        for c in captured:
            code_counts[c] = code_counts.get(c, 0) + 1
        best_code = max(code_counts, key=code_counts.get)
        count = code_counts[best_code]

        codes[btn_name] = best_code
        print(f"  [{btn_name}]: {best_code[:40]} "
              f"({count}/{len(captured)} packets)")

    save_button_codes(codes)
    print(f"\n  Saved {len(codes)} button codes to {BUTTON_CODE_FILE}")
    return codes


# ═══════════════════════════════════════════════════════════════════════
# Main Controller
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  Enbrighten LED Cafe Lights — RF Remote Controller")
    print(f"  {NUM_LEDS} LEDs | Channel {RF_CHANNEL} | "
          f"[{' '.join(f'{b:02X}' for b in NRF24_ADDR)}]")
    print("=" * 70)

    # ── Initialize radio on SPI0 ──
    spi_radio = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi_radio, csn, ce)

    # ── Initialize LEDs on SPI1 ──
    spi_led = SpiDev()
    spi_led.open(1, 0)
    spi_led.max_speed_hz = LED_SPI_SPEED
    spi_led.mode = 0b00
    spi_led.lsbfirst = False
    print(f"  LED SPI: {spi_led.max_speed_hz / 1e6:.3f} MHz")

    # ── Load button codes ──
    button_codes = load_button_codes()
    learn_mode = "--learn" in sys.argv

    # ── Pair with remote ──
    radio.configure()

    if learn_mode:
        pair_remote(radio)
        button_codes = learn_buttons(radio)
    elif not button_codes:
        print("\n  No button codes found in button_codes.json.")
        print("  Run with --learn to capture codes from the remote.")
        print("  Pairing anyway — unrecognized packets will be logged.\n")
        pair_remote(radio)
    else:
        pair_remote(radio)

    hex_to_button = {v: k for k, v in button_codes.items()}
    print(f"  {len(hex_to_button)} button codes loaded.\n")

    # ── State ──
    dimmer_idx = 9
    current_wrgb = None
    active_mode = None
    modes_idx = 0
    white_idx = 2
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

    last_button_code = None
    last_button_time = 0

    # ── SPI output loop (continuous refresh) ──
    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi_led.xfer2(buf[:])

    # ── Animation: Fade ──
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

    # ── Animation: Strobe ──
    def strobe_loop(start_color):
        current = start_color
        with lock:
            state['buf'] = build_solid_buf(current, 1.0)
        while not mode_stop.wait(STROBE_INTERVAL):
            current = pick_random(exclude=current)
            with lock:
                state['buf'] = build_solid_buf(current, 1.0)

    # ── Animation: Chaser ──
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

    # ── Animation: Theater Chase ──
    def theater_loop():
        offset = 0
        while not mode_stop.is_set():
            pixels = []
            for n in range(NUM_LEDS):
                pixels.append(NEUTRAL_WHITE if n % 3 == offset else OFF)
            with lock:
                state['buf'] = build_multi_buf(pixels)
            if mode_stop.wait(THEATER_INTERVAL):
                return
            offset = (offset + 1) % 3

    # ── Animation: Twinkle ──
    def twinkle_loop():
        pcb_sequences = []
        pcb_delays = []
        pcb_colors = []
        for _ in range(NUM_LEDS):
            sc = pick_random()
            pcb_colors.append(sc)
            seq = [sc]
            prev = sc
            for _ in range(TWINKLE_COLOR_COUNT):
                nxt = pick_random(exclude=prev)
                seq.append(nxt)
                prev = nxt
            pcb_sequences.append(seq)
            pcb_delays.append(random.uniform(1.0, 4.0))

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

    # ── Static preset pattern ──
    def preset_static_loop(pattern):
        pixels = [pattern[n % len(pattern)] for n in range(NUM_LEDS)]
        with lock:
            state['buf'] = build_multi_buf(pixels)
        while not mode_stop.wait(0.5):
            pass

    # ── Mode control ──
    def stop_mode():
        nonlocal active_mode, mode_thread
        if mode_thread:
            mode_stop.set()
            mode_thread.join(timeout=2)
            mode_thread = None
        active_mode = None
        mode_stop.clear()

    def start_mode(mode, start_color=None, pattern=None):
        nonlocal active_mode, mode_thread
        stop_mode()
        active_mode = mode
        if mode == 'fade':
            sc = start_color or pick_random()
            mode_thread = threading.Thread(
                target=fade_loop, args=(sc,), daemon=True)
        elif mode == 'strobe':
            sc = start_color or pick_random()
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
                target=preset_static_loop, args=(pattern,), daemon=True)
        if mode_thread:
            mode_thread.start()

    # ── Timer ──
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
            'wrgb': current_wrgb, 'mode': active_mode,
            'dimmer_idx': dimmer_idx, 'modes_idx': modes_idx,
            'dual_color1': dual_color1, 'dual_color2': dual_color2,
        }
        stop_mode()
        current_wrgb = None
        with lock:
            state['buf'] = build_solid_buf(None, 1.0)
        power_on = False
        timer_thread = None
        timer_label = None
        print("  Timer expired — Power: OFF")

    def start_timer(hours, label):
        nonlocal timer_thread, timer_label
        cancel_timer()
        timer_label = label

        def _wait():
            if not timer_cancel.wait(hours * 3600):
                timer_fire()
        timer_thread = threading.Thread(target=_wait, daemon=True)
        timer_thread.start()

    # ── Button handler ──
    def handle_button(btn_name):
        nonlocal power_on, saved_setting, current_wrgb, dimmer_idx
        nonlocal active_mode, modes_idx, white_idx
        nonlocal pending_slot, dual_color1, dual_color2

        # ─── Power ───
        if btn_name == "Power":
            if power_on:
                saved_setting = {
                    'wrgb': current_wrgb, 'mode': active_mode,
                    'dimmer_idx': dimmer_idx, 'modes_idx': modes_idx,
                    'dual_color1': dual_color1, 'dual_color2': dual_color2,
                }
                cancel_timer()
                stop_mode()
                current_wrgb = None
                with lock:
                    state['buf'] = build_solid_buf(None, 1.0)
                power_on = False
                print("  Power: OFF")
            else:
                power_on = True
                if saved_setting:
                    dimmer_idx = saved_setting['dimmer_idx']
                    modes_idx = saved_setting['modes_idx']
                    dual_color1 = saved_setting['dual_color1']
                    dual_color2 = saved_setting['dual_color2']
                    mode = saved_setting['mode']
                    if mode == 'dual':
                        active_mode = 'dual'
                        with lock:
                            state['buf'] = build_dual_buf(
                                dual_color1, dual_color2,
                                DIMMER_STEPS[dimmer_idx])
                        print("  Power: ON (dual)")
                    elif mode:
                        if mode == 'preset':
                            _, _, pat = MODES_LIST[modes_idx]
                            start_mode('preset', pattern=pat)
                        elif mode in ('fade', 'strobe'):
                            start_mode(mode, saved_setting['wrgb'])
                        else:
                            start_mode(mode)
                        print(f"  Power: ON ({mode})")
                    else:
                        current_wrgb = saved_setting['wrgb']
                        with lock:
                            state['buf'] = build_solid_buf(
                                current_wrgb, DIMMER_STEPS[dimmer_idx])
                        print(f"  Power: ON "
                              f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})")
                else:
                    current_wrgb = NEUTRAL_WHITE
                    with lock:
                        state['buf'] = build_solid_buf(
                            current_wrgb, DIMMER_STEPS[dimmer_idx])
                    print("  Power: ON (Neutral White)")
            return

        if not power_on:
            return

        # ─── Color buttons ───
        if btn_name in COLOR_BUTTONS:
            wrgb = COLOR_BUTTONS[btn_name]
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
                c1n = COLOR_NAMES.get(dual_color1, '—')
                c2n = COLOR_NAMES.get(dual_color2, '—')
                print(f"  Dual: C1={c1n}, C2={c2n} (dim {dim:.1f})")
            else:
                stop_mode()
                dual_color1 = None
                dual_color2 = None
                current_wrgb = wrgb
                with lock:
                    state['buf'] = build_solid_buf(
                        current_wrgb, DIMMER_STEPS[dimmer_idx])
                print(f"  -> {COLOR_NAMES.get(wrgb, btn_name)} "
                      f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})")
            return

        # ─── White Select (cycle temperatures) ───
        if btn_name == "White_Select":
            white_idx = (white_idx + 1) % len(WHITE_TEMPS)
            name, wrgb = WHITE_TEMPS[white_idx]
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
                c1n = COLOR_NAMES.get(dual_color1, '—')
                c2n = COLOR_NAMES.get(dual_color2, '—')
                print(f"  Dual: C1={c1n}, C2={c2n} (dim {dim:.1f})")
            else:
                stop_mode()
                dual_color1 = None
                dual_color2 = None
                current_wrgb = wrgb
                with lock:
                    state['buf'] = build_solid_buf(
                        current_wrgb, DIMMER_STEPS[dimmer_idx])
                print(f"  -> {name} "
                      f"(dim {DIMMER_STEPS[dimmer_idx]:.1f})")
            return

        # ─── Color 1 / Color 2 (dual-color slots) ───
        if btn_name == "Color1":
            pending_slot = 'color1'
            print("  Color 1: select color for odd PCBs (D1, D3, D5)...")
            return
        if btn_name == "Color2":
            pending_slot = 'color2'
            print("  Color 2: select color for even PCBs (D2, D4, D6)...")
            return

        # ─── Timers ───
        if btn_name == "2-hour":
            start_timer(2, "2-hour")
            print("  Timer: 2-hour")
            return
        if btn_name == "4-hour":
            start_timer(4, "4-hour")
            print("  Timer: 4-hour")
            return

        # ─── Fade ───
        if btn_name == "Fade":
            dimmer_idx = 9
            sc = current_wrgb if (
                active_mode is None and current_wrgb) else None
            start_mode('fade', sc)
            print("  Fade: ON")
            return

        # ─── Dimming ───
        if btn_name == "Dimming":
            if active_mode in ('fade', 'strobe', 'theater',
                               'twinkle', 'preset'):
                return
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
                    state['buf'] = build_solid_buf(current_wrgb, dim)
            print(f"  Dimmer: {dim:.1f}")
            return

        # ─── Strobe ───
        if btn_name == "Strobe":
            dimmer_idx = 9
            sc = current_wrgb if (
                active_mode is None and current_wrgb) else None
            start_mode('strobe', sc)
            print("  Strobe: ON")
            return

        # ─── Modes (cycle presets + effects) ───
        if btn_name == "Modes":
            dimmer_idx = 9
            if active_mode in ('preset', 'chaser', 'theater', 'twinkle'):
                modes_idx = (modes_idx + 1) % len(MODES_LIST)
            else:
                modes_idx = 0
            name, mode_type, pattern = MODES_LIST[modes_idx]
            start_mode(mode_type, pattern=pattern)
            print(f"  Mode: {name}")
            return

    # ── Start SPI output thread ──
    spi_thread = threading.Thread(target=spi_loop, daemon=True)
    spi_thread.start()

    print(f"  Power: OFF | Dimmer: {DIMMER_STEPS[dimmer_idx]:.1f}")
    print("  Waiting for button presses... (Ctrl+C to quit)\n")

    # ── Main RF receive loop ──
    try:
        while True:
            if radio.available():
                raw = radio.read()
                desc = descramble(raw)

                if is_background_packet(desc):
                    continue

                code = extract_button_code(desc)
                now = time.monotonic()

                if (code == last_button_code
                        and (now - last_button_time) < DEBOUNCE_S):
                    continue
                last_button_code = code
                last_button_time = now

                btn_name = hex_to_button.get(code)
                if btn_name:
                    handle_button(btn_name)
                else:
                    print(f"  Unknown: {code[:48]}"
                          f"{'...' if len(code) > 48 else ''}")

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        cancel_timer()
        mode_stop.set()
        state['running'] = False
        spi_thread.join(timeout=1)
        if mode_thread:
            mode_thread.join(timeout=1)
        with lock:
            state['buf'] = build_solid_buf(None, 1.0)
        spi_led.xfer2(state['buf'][:])
        radio.power_down()
        spi_led.close()
        print("  Radio off, LEDs off. Goodbye.")


if __name__ == "__main__":
    main()
