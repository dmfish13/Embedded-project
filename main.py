#!/usr/bin/env python3
"""
Enbrighten LED Cafe Lights — RF Remote Controller

System Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                      Raspberry Pi 5                              │
    │                                                                  │
    │  ┌──────────────┐       main.py        ┌──────────────────┐    │
    │  │  nRF24L01+   │◄── SPI0 (1 MHz) ───►│  Main Thread     │    │
    │  │  RF Receiver │    GPIO D8 (CSN)      │  (RF receive +   │    │
    │  │              │    GPIO D25 (CE)       │   button dispatch)│   │
    │  └──────┬───────┘                       └────────┬─────────┘    │
    │         │                                        │              │
    │         │ 2.4 GHz                       ┌────────┴─────────┐    │
    │         │ Ch 42                         │  SPI Output Thread│    │
    │         │                               │  (continuous LED  │    │
    │  ┌──────┴───────┐                       │   frame refresh)  │    │
    │  │ Jasco Remote │                       └────────┬─────────┘    │
    │  │ (25 buttons) │                                │              │
    │  │ XNS1042      │                       SPI1 (2 MHz)           │
    │  └──────────────┘                       GPIO 20 (MOSI)         │
    │                                                  │              │
    │                                         ┌────────┴─────────┐    │
    │                                         │ 3.3V→5V Shifter  │    │
    │                                         └────────┬─────────┘    │
    │                                                  │              │
    └──────────────────────────────────────────────────┼──────────────┘
                                                       │
                                              ┌────────┴─────────┐
                                              │  TM1815B RGBW    │
                                              │  LED String      │
                                              │  (6 LEDs/PCBs)   │
                                              └──────────────────┘

Threading Model:
    1. Main Thread — RF receive loop: polls nRF24L01+ FIFO, descrambles
       packets, debounces, dispatches to handle_button()
    2. SPI Output Thread — continuously pushes the current LED frame buffer
       to SPI1 (TM1815B requires constant refresh to maintain output)
    3. Mode Thread — runs the active animation (fade/strobe/chaser/theater/
       twinkle/preset). Writes new frame buffers under lock. Stopped via
       mode_stop Event when switching modes.
    4. Timer Thread — optional 2hr/4hr auto-off countdown

RF Protocol (XNS1042 / XN297L → nRF24L01+ compatibility):
    The Jasco remote uses an XNS1042 transmitter (XN297L protocol).
    On-air packets are scrambled with Scramble Table B and bit-reversed.
    The nRF24L01+ receives raw bytes which must be descrambled:
      descrambled[i] = bit_reverse(raw[i]) XOR SCRAMBLE_B[ADDR_WIDTH + i]

    Address filtering: The nRF24L01+ only accepts packets whose on-air
    preamble + address matches [38 72 2D A8 5E]. This is the hardware
    "pairing" lock — no promiscuous mode, no other devices heard.

LED Protocol (TM1815B):
    Single-wire protocol encoded via SPI bit-banging at 2.0 MHz.
    Each data bit becomes 4 SPI bits:
      Logic 1 → 0b0001 (LOW 1500ns, HIGH 500ns)
      Logic 0 → 0b0111 (LOW 500ns, HIGH 1500ns)
    Frame: [RESET 0xFF*80][C1 4B][C2 4B][D1..D6 16B each][RESET 0xFF*80]
    Color order per LED: W, R, G, B (one byte each, MSB first)

Button Mapping (25 buttons on the Jasco QOBRGBXYZA remote):
    Row 1: Power | Fade | Dimming | Strobe
    Row 2: Color1 | 2-hour | Color2 | 4-hour
    Row 3: Modes | Deep Red | Mint | Dark Blue
    Row 4: Red | Orange | Light Blue | Violet
    Row 5: Green | Golden Rod | Cyan | Purple
    Row 6: Blue | Yellow | Steel Blue | Magenta
    Row 7: White

Usage:
    python3 main.py            # Normal operation (requires button_codes.json)
    python3 main.py --learn    # Interactive capture of button hex codes
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

# Number of RGBW LEDs (PCBs) in the Enbrighten cafe light string
NUM_LEDS = 6

# TM1815B current-setting registers:
# C1 sets the max current per channel (6-bit, 0x20 = ~50% of 30mA)
# C2 is the bitwise complement of C1 (protocol requirement)
C1 = [0x20, 0x20, 0x20, 0x20]
C2 = [0xDF, 0xDF, 0xDF, 0xDF]

# SPI clock for LED data output — 2.0 MHz hits a clean Pi 5 clock divider
# and produces gap-free DMA transfers (1.6 MHz has inter-byte gaps)
LED_SPI_SPEED = 2_000_000

# Number of 0xFF bytes for reset pulse (idle-HIGH, >200µs at 2 MHz = 80 bytes)
RESET = 80

# Brightness levels cycled by the Dimming button (1.0 → 0.1 → wraps to 1.0)
DIMMER_STEPS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

# Animation timing parameters (seconds)
FADE_DURATION = 1.5       # Time to crossfade between two colors
FADE_HOLD = 0.5           # Pause on each color before starting next fade
FADE_STEP_INTERVAL = 0.03 # ~33 FPS interpolation steps during fade

STROBE_INTERVAL = 1.25    # Time between random color switches

CHASER_INTERVAL = 0.35    # Time between color shifts through the LED chain

THEATER_INTERVAL = 0.3    # Time between steps in theater chase pattern

TWINKLE_FADE_DOWN = 1.25  # Time to fade a single LED to OFF
TWINKLE_FADE_UP = 1.25    # Time to fade a single LED to new color
TWINKLE_HOLD = 0.5        # Hold time at full brightness before next fade
TWINKLE_TICK = 0.03       # Animation tick interval (~33 FPS)
TWINKLE_COLOR_COUNT = 200 # Pre-generated random color sequence length per LED


# ═══════════════════════════════════════════════════════════════════════
# RF Constants (XNS1042 / XN297L protocol)
#
# The XNS1042 IC in the Jasco remote uses the XN297L protocol, which
# applies two transformations to payload bytes before transmission:
#   1. Bit reversal — each byte's bit order is flipped (MSB↔LSB)
#   2. XOR with Scramble Table B — a fixed 32-byte sequence applied
#      starting at offset ADDR_WIDTH (bytes 0-4 are the address)
#
# The nRF24L01+ receiver gets raw bytes that must be un-done:
#   descrambled[i] = bit_reverse(raw[i]) XOR SCRAMBLE_B[5 + i]
# ═══════════════════════════════════════════════════════════════════════

# Lookup table: BIT_REVERSE[0xC0] = 0x03 (reverses all 8 bits)
BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])

# XN297L Scramble Table B — XOR'd with payload after bit reversal
# 32 bytes covers address (5) + max payload (27 meaningful bytes)
SCRAMBLE_B = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
]

# 5-byte receive address — must match the remote's TX address exactly.
# The nRF24L01+ hardware compares incoming preamble+address against this;
# non-matching packets are silently dropped (this IS the pairing lock).
NRF24_ADDR = [0x38, 0x72, 0x2D, 0xA8, 0x5E]
ADDR_WIDTH = 5

# RF channel 42 = 2442 MHz (2400 + 42)
RF_CHANNEL = 42

# Known background source packet headers (descrambled first 4 bytes).
# These come from an unrelated transmitter sharing the same address/channel.
# They are filtered out so only the remote's actual signal is processed.
COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])
IDLE_HEADER = bytes([0x4C, 0xED, 0xAD, 0x0B])

# Debounce: ignore repeated identical packets within this window.
# The remote sends bursts while a button is held; we want one action per press.
DEBOUNCE_S = 0.30


# ═══════════════════════════════════════════════════════════════════════
# Color Definitions — WRGB (White, Red, Green, Blue)
#
# TM1815B frame order is W, R, G, B — all tuples follow this convention.
# Values 0-255 represent PWM duty cycle per channel before dimming.
# ═══════════════════════════════════════════════════════════════════════

OFF           = (0,   0,   0,   0)
NEUTRAL_WHITE = (255, 0,   0,   0)   # Pure white LED channel only
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
# White temperature variants — mix of W channel + RGB to simulate CCT
CANDLELIGHT   = (76,  255, 128, 0)   # ~1800K warm amber
WARM_WHITE    = (154, 180, 77,  0)   # ~3000K incandescent
COOL_WHITE    = (217, 0,   64,  128) # ~5000K fluorescent
DAYLIGHT      = (178, 0,   128, 255) # ~6500K blue-white

# Holiday preset colors (not on individual buttons)
FOREST_GREEN  = (0,   10,  154, 24)
IRISH_GREEN   = (0,   30,  196, 30)

# White temperatures cycled by the White button (ordered warm → cool)
# Starts at index 2 (Neutral White); each press advances to next
WHITE_TEMPS = [
    ("Candlelight ~1800K",   CANDLELIGHT),
    ("Warm White ~3000K",    WARM_WHITE),
    ("Neutral White ~4000K", NEUTRAL_WHITE),
    ("Cool White ~5000K",    COOL_WHITE),
    ("Daylight ~6500K",      DAYLIGHT),
]

# Maps button names to WRGB values for the 15 direct-color buttons
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

# Colors eligible for random selection in Fade/Strobe/Chaser/Twinkle.
# Excludes white temperatures (Candlelight, Warm/Cool/Daylight) since
# they look washed out in rapid animations. Includes Neutral White.
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
#
# Each press of Modes advances to the next entry. Format:
#   (display_name, mode_type, color_pattern_or_None)
#
# "preset" entries display a static repeating color pattern across LEDs.
# "chaser"/"theater"/"twinkle" entries run their respective animations.
# These were originally separate buttons but are now unified under Modes.
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

# All 25 button names in the order they appear on the remote (used by --learn)
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

# JSON file mapping button names → descrambled hex codes (created by --learn)
BUTTON_CODE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "button_codes.json")


# ═══════════════════════════════════════════════════════════════════════
# TM1815B LED Encoding (4-bit protocol @ 2.0 MHz)
#
# The TM1815B uses a single-wire return-to-zero protocol where the line
# idles HIGH and data is sent as LOW pulses of varying width:
#   Logic 1: LOW ~1500ns, HIGH ~500ns  → SPI bits 0001
#   Logic 0: LOW ~500ns,  HIGH ~1500ns → SPI bits 0111
#
# At 2.0 MHz, each SPI bit = 500ns, so 4 SPI bits = one data bit (2µs).
# One color byte (8 data bits) = 32 SPI bits = 4 SPI bytes.
#
# A pre-computed lookup table (LUT_4BIT) maps each possible byte value
# (0-255) to its 4-byte SPI encoding, avoiding per-frame computation.
# ═══════════════════════════════════════════════════════════════════════

def encode_byte_4bit(value):
    """Encode a single byte (0-255) into 4 SPI bytes for TM1815B."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001  # Logic 1: long LOW
        else:
            encoded = (encoded << 4) | 0b0111  # Logic 0: short LOW
    return bytes([
        (encoded >> 24) & 0xFF, (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF, encoded & 0xFF,
    ])


# Pre-computed encoding for all 256 possible byte values
LUT_4BIT = [encode_byte_4bit(v) for v in range(256)]


def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes=80):
    """Build a complete TM1815B frame ready for SPI transmission.

    Frame structure: [RESET][C1][C2][D1][D2]...[D6][RESET]
    - RESET: 0xFF bytes (line HIGH) for >200µs latch period
    - C1: 4 bytes setting max current per WRGB channel
    - C2: bitwise complement of C1 (error-detection)
    - D1-D6: 16 bytes each (4 bytes per color × 4 colors WRGB)
    """
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
    """Scale a WRGB pixel by a dimmer factor (0.0 to 1.0)."""
    return (
        int(wrgb[0] * dimmer), int(wrgb[1] * dimmer),
        int(wrgb[2] * dimmer), int(wrgb[3] * dimmer),
    )


def lerp_pixel(a, b, t):
    """Linearly interpolate between two WRGB pixels (t=0→a, t=1→b)."""
    return (
        int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t), int(a[3] + (b[3] - a[3]) * t),
    )


def pick_random(exclude=None):
    """Pick a random color from ELIGIBLE_COLORS, excluding one to avoid repeats."""
    choices = [c for c in ELIGIBLE_COLORS if c != exclude]
    return random.choice(choices)


def build_solid_buf(wrgb, dimmer):
    """Build frame buffer with all LEDs set to the same color+dimmer."""
    pixel = dim_pixel(wrgb, dimmer) if wrgb else OFF
    return list(build_frame(C1, C2, [pixel] * NUM_LEDS, LUT_4BIT,
                            reset_bytes=RESET))


def build_multi_buf(pcb_pixels):
    """Build frame buffer with individually-addressed LED colors."""
    return list(build_frame(C1, C2, pcb_pixels, LUT_4BIT,
                            reset_bytes=RESET))


def build_dual_buf(color1, color2, dimmer):
    """Build frame buffer with alternating colors (odd PCBs=color1, even=color2)."""
    pixels = []
    for n in range(NUM_LEDS):
        c = (color1 if color1 else OFF) if n % 2 == 0 \
            else (color2 if color2 else OFF)
        pixels.append(dim_pixel(c, dimmer))
    return list(build_frame(C1, C2, pixels, LUT_4BIT, reset_bytes=RESET))


# ═══════════════════════════════════════════════════════════════════════
# nRF24L01+ Driver (raw SPI register access via Blinka)
#
# Uses Adafruit Blinka (board/busio/digitalio) to talk to the nRF24L01+
# on SPI0. This is a minimal driver — only RX mode is implemented since
# this controller only receives from the remote, never transmits.
#
# Pin assignments:
#   SPI0 SCK/MOSI/MISO = default Pi 5 pins (GPIO 11/10/9)
#   CSN = GPIO D8 (active-low chip select, directly driven)
#   CE  = GPIO D25 (chip enable — HIGH to enter RX mode)
#
# The nRF24L01+ requires manual CSN toggling around each SPI transaction
# (not handled by the SPI peripheral's built-in CS). Each method acquires
# the SPI bus lock, pulls CSN low, does the transfer, pulls CSN high.
# ═══════════════════════════════════════════════════════════════════════

class NRF24L01:
    def __init__(self, spi, csn, ce):
        self._spi = spi
        self._csn = csn
        self._ce = ce
        self._csn.direction = digitalio.Direction.OUTPUT
        self._csn.value = True   # CSN idle HIGH (deselected)
        self._ce.direction = digitalio.Direction.OUTPUT
        self._ce.value = False   # CE idle LOW (standby)
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000, polarity=0, phase=0)
        self._spi.unlock()

    def _reg_read(self, reg):
        """Read a single register (command byte = register address)."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        buf = bytearray([reg, 0xFF])  # Send reg addr, clock out response
        self._spi.write_readinto(buf, buf)
        self._csn.value = True
        self._spi.unlock()
        return buf[1]

    def _reg_write(self, reg, value):
        """Write a single register (command = 0x20 | reg)."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg, value]))
        self._csn.value = True
        self._spi.unlock()

    def _reg_write_bytes(self, reg, data):
        """Write multiple bytes to a register (e.g., RX address)."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg]) + bytearray(data))
        self._csn.value = True
        self._spi.unlock()

    def _cmd(self, cmd):
        """Send a command byte with no data (e.g., flush FIFO)."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([cmd]))
        self._csn.value = True
        self._spi.unlock()

    def _read_payload(self, length):
        """Read the RX payload (command 0x61 = R_RX_PAYLOAD)."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        tx = bytearray([0x61] + [0xFF] * length)
        rx = bytearray(len(tx))
        self._spi.write_readinto(tx, rx)
        self._csn.value = True
        self._spi.unlock()
        return rx[1:]  # First byte is status, payload starts at [1]

    def configure(self):
        """Configure for XN297L reception on channel 42.

        Sets up the radio in RX mode with:
        - Hardware address filtering on pipe 0 (non-promiscuous)
        - 5-byte address width matching the remote's TX address
        - Fixed 32-byte payload (XN297L always sends full frames)
        - 1 Mbps data rate, no CRC, no auto-ack, no dynamic payload
        - Maximum receiver sensitivity (-82 dBm at 1 Mbps)

        Only packets whose on-air address matches NRF24_ADDR will trigger
        a FIFO entry. This is the hardware pairing mechanism — no other
        device can be heard unless it transmits on the same address.
        """
        self._ce.value = False
        self._reg_write(0x00, 0x03)          # CONFIG: PWR_UP | PRIM_RX
        time.sleep(0.002)                    # 1.5ms power-up delay
        self._reg_write(0x01, 0x00)          # EN_AA: no auto-ack (XN297L compat)
        self._reg_write(0x02, 0x01)          # EN_RXADDR: pipe 0 only
        self._reg_write(0x03, ADDR_WIDTH - 2)  # SETUP_AW: 5-byte address
        self._reg_write_bytes(0x0A, bytes(NRF24_ADDR))  # RX_ADDR_P0
        self._reg_write(0x11, 32)            # RX_PW_P0: 32-byte payload
        self._reg_write(0x06, 0x07)          # RF_SETUP: 1Mbps, 0dBm
        self._reg_write(0x1C, 0x00)          # DYNPD: no dynamic payload
        self._reg_write(0x1D, 0x00)          # FEATURE: all features off
        self._cmd(0xE2)                      # FLUSH_RX
        self._cmd(0xE1)                      # FLUSH_TX
        self._reg_write(0x07, 0x70)          # STATUS: clear RX_DR/TX_DS/MAX_RT
        self._reg_write(0x05, RF_CHANNEL)    # RF_CH: channel 42 (2442 MHz)
        self._ce.value = True                # CE HIGH → enter RX mode

    def available(self):
        """Check if the RX FIFO has data (FIFO_STATUS bit 0 = RX_EMPTY)."""
        fifo = self._reg_read(0x17)
        return not bool(fifo & 0x01)

    def read(self):
        """Read 32-byte payload from RX FIFO and clear status flags."""
        data = self._read_payload(32)
        self._reg_write(0x07, 0x70)  # Clear interrupt flags
        return data

    def power_down(self):
        """Enter power-down mode (CE LOW, PWR_UP=0). ~900nA standby."""
        self._ce.value = False
        self._reg_write(0x00, 0x00)


# ═══════════════════════════════════════════════════════════════════════
# RF Descramble (XN297L Scramble Table B + bit reversal)
#
# The nRF24L01+ receives raw on-air bytes. The XNS1042 transmitter in the
# remote applied: scramble(data) = bit_reverse(data[i]) XOR SCRAMBLE_B[5+i]
# We reverse that to recover the original payload the remote intended to send.
# ═══════════════════════════════════════════════════════════════════════

def descramble(raw):
    """Convert raw nRF24L01+ bytes → original XN297L payload.

    For each byte: bit-reverse it, then XOR with the scramble table entry
    at offset (ADDR_WIDTH + byte_index). Bytes beyond the scramble table
    length are only bit-reversed (no XOR).
    """
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
    """Inverse of descramble — convert descrambled payload back to raw on-air bytes.

    Used by find_idle_start() to detect idle-line tail patterns in the
    raw (on-air) domain where they appear as repeating 0x55/0xAA/0xFF.
    """
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
    """Convert bytes to uppercase hex string (e.g., b'\\xAB\\xCD' → 'ABCD')."""
    return "".join(f"{b:02X}" for b in data)


def find_idle_start(desc_bytes):
    """Find where meaningful payload ends and idle-line tail begins.

    The XN297L transmits fixed-length frames. After the actual button data,
    the remaining bytes are idle-line fill (raw values 0xFF, 0x55, or 0xAA).
    This function scans backward from the end of the raw bytes to find where
    the tail starts, allowing ±1 bit error tolerance for RF noise.

    Returns the byte index where idle tail begins (= length of meaningful data).
    """
    raw = to_raw(desc_bytes)
    if len(raw) < 5:
        return len(raw)
    tail_val = raw[-1]
    if tail_val not in (0xFF, 0x55, 0xAA):
        return len(raw)  # Last byte isn't an idle pattern — all bytes are data
    start = len(raw)
    for i in range(len(raw) - 2, -1, -1):
        if raw[i] == tail_val or bin(raw[i] ^ tail_val).count('1') <= 1:
            start = i  # This byte is part of the idle tail
        else:
            break      # Found end of actual data
    return start


def is_background_packet(desc):
    """Check if a descrambled packet is from the known background source.

    A separate unidentified transmitter shares the same address/channel and
    sends packets with these two header patterns at ~30/s. We filter them
    out so only the Jasco remote's actual button presses are processed.
    """
    return desc[:4] == COMMAND_HEADER or desc[:4] == IDLE_HEADER


def extract_button_code(desc):
    """Extract the button-identifying hex string from a descrambled packet.

    Strips the idle-line tail and converts the meaningful payload portion
    to a hex string. This string is used as the key in button_codes.json
    to map packets → button names.
    """
    idle_at = find_idle_start(desc)
    return to_hex(desc[:idle_at])


# ═══════════════════════════════════════════════════════════════════════
# Button Code Storage (button_codes.json)
#
# The mapping between RF hex codes and button names is stored in a JSON
# file alongside main.py. Format: {"Button_Name": "HEX_STRING", ...}
# Created by --learn mode, loaded on each normal startup.
# ═══════════════════════════════════════════════════════════════════════

def load_button_codes():
    """Load button name → hex code mapping from disk. Returns {} if missing."""
    if os.path.exists(BUTTON_CODE_FILE):
        try:
            with open(BUTTON_CODE_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_button_codes(codes):
    """Persist button name → hex code mapping to disk."""
    with open(BUTTON_CODE_FILE, 'w') as f:
        json.dump(codes, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════
# Pairing — confirm the remote is reachable
#
# "Pairing" for this system means confirming the remote is present and
# transmitting. The actual security/lock mechanism is the nRF24L01+'s
# hardware address filter — it will ONLY receive packets matching
# NRF24_ADDR [38 72 2D A8 5E]. No software "pairing" is needed to lock
# out other devices; the address match does that at the hardware level.
#
# This function waits for two non-background packets to arrive within 5s
# of each other, confirming the remote is nearby and operational.
# ═══════════════════════════════════════════════════════════════════════

def pair_remote(radio):
    """Wait for a valid remote packet to confirm the remote is present.

    Strategy:
    1. Listen for any packet that passes address filtering
    2. Discard known background source packets (CMD/IDLE headers)
    3. On first non-background packet, wait up to 5s for a second one
    4. Two packets confirms the remote is actively transmitting
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

            # Skip background source packets
            if is_background_packet(desc):
                bg_count += 1
                continue

            # Got a non-background packet — try to confirm with a second one
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

            # Timeout waiting for confirmation — ask user to try again
            print("  Got one packet but couldn't confirm. "
                  "Press a button again...")
            start = time.monotonic()
            bg_count = 0

        # Periodic status update so user knows it's working
        now = time.monotonic()
        if now - last_status >= 10.0:
            elapsed = now - start
            print(f"    Still scanning... "
                  f"({elapsed:.0f}s, {bg_count} background filtered)")
            last_status = now

        time.sleep(0.001)


# ═══════════════════════════════════════════════════════════════════════
# Learning Mode — capture hex codes for each button
#
# Guides the user through pressing each of the 25 buttons one at a time.
# For each button:
#   1. User holds the button and presses Enter
#   2. Radio captures packets for 2 seconds
#   3. Background packets are filtered out
#   4. The most frequently seen hex code is stored as that button's ID
#
# Results are saved to button_codes.json. On subsequent runs, this file
# is loaded to map received packets to button actions.
# ═══════════════════════════════════════════════════════════════════════

def learn_buttons(radio):
    """Interactive button code capture — prompts for each of 25 buttons."""
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

        # Collect all non-background packets during the capture window
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

        # Use the most common hex code (majority vote for noise resilience)
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
#
# Execution flow:
#   1. Initialize hardware (SPI0 for radio, SPI1 for LEDs)
#   2. Load button codes from JSON (or enter learn mode)
#   3. Pair with remote (confirm it's present)
#   4. Start SPI output thread (continuous LED refresh)
#   5. Enter RF receive loop (poll → descramble → debounce → dispatch)
#
# State is managed via closure variables in main(). The handle_button()
# function modifies state and updates the shared frame buffer under lock.
# The SPI output thread continuously pushes whatever buffer is current.
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  Enbrighten LED Cafe Lights — RF Remote Controller")
    print(f"  {NUM_LEDS} LEDs | Channel {RF_CHANNEL} | "
          f"[{' '.join(f'{b:02X}' for b in NRF24_ADDR)}]")
    print("=" * 70)

    # ── Initialize radio on SPI0 (Blinka/busio for nRF24L01+ compatibility) ──
    spi_radio = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi_radio, csn, ce)

    # ── Initialize LEDs on SPI1 (spidev for gap-free DMA transfers) ──
    spi_led = SpiDev()
    spi_led.open(1, 0)       # Bus 1, device 0 → GPIO 20 (MOSI)
    spi_led.max_speed_hz = LED_SPI_SPEED
    spi_led.mode = 0b00      # CPOL=0, CPHA=0
    spi_led.lsbfirst = False  # MSB first (TM1815B requirement)
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

    # ── Controller State ──
    # All state variables are closure-captured by handle_button() and
    # animation threads. Modified via 'nonlocal' declarations.
    dimmer_idx = 9           # Index into DIMMER_STEPS (9 = 1.0 = full)
    current_wrgb = None      # Current solid color (None = off/black)
    active_mode = None       # Active animation: 'fade'/'strobe'/'chaser'/
                             # 'theater'/'twinkle'/'preset'/'dual'/None
    modes_idx = 0            # Current position in MODES_LIST cycle
    white_idx = 2            # Current position in WHITE_TEMPS (2=Neutral)
    power_on = False         # Master power state
    saved_setting = None     # State snapshot for power-off/restore
    pending_slot = None      # 'color1'/'color2' when waiting for next color pick
    dual_color1 = None       # WRGB for odd PCBs in dual-color mode
    dual_color2 = None       # WRGB for even PCBs in dual-color mode
    timer_thread = None      # Auto-off timer thread (2hr or 4hr)
    timer_cancel = threading.Event()  # Cancels the active timer
    timer_label = None       # "2-hour" or "4-hour" for display

    # Shared frame buffer — written by handle_button/animation threads,
    # read by the SPI output thread. Protected by 'lock'.
    state = {'buf': build_solid_buf(None, 1.0), 'running': True}
    lock = threading.Lock()
    mode_stop = threading.Event()  # Signals animation thread to stop
    mode_thread = None             # Current animation thread reference

    # Debounce tracking for RF button presses
    last_button_code = None
    last_button_time = 0

    # ── SPI output loop (continuous refresh) ──
    # TM1815B LEDs require constant re-transmission of the frame to maintain
    # their output. This thread runs as fast as SPI allows (~500 frames/s at
    # 2 MHz with 6 LEDs), ensuring rock-solid LED output with no flicker.
    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi_led.xfer2(buf[:])

    # ── Animation: Fade ──
    # Smoothly crossfades between random colors. Each transition takes
    # FADE_DURATION seconds with FADE_STEP_INTERVAL between interpolation
    # steps (~50 steps per fade). Holds each color for FADE_HOLD seconds.
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
    # Instantly switches all LEDs to a new random color every STROBE_INTERVAL.
    # No fade/transition — hard cut for a strobe effect.
    def strobe_loop(start_color):
        current = start_color
        with lock:
            state['buf'] = build_solid_buf(current, 1.0)
        while not mode_stop.wait(STROBE_INTERVAL):
            current = pick_random(exclude=current)
            with lock:
                state['buf'] = build_solid_buf(current, 1.0)

    # ── Animation: Chaser ──
    # Colors shift through the LED chain like a marquee. Each tick, every LED
    # takes the color of its left neighbor, and LED[0] gets a new random color.
    # Respects the current dimmer setting (updated live each tick).
    def chaser_loop():
        dim = DIMMER_STEPS[dimmer_idx]
        pcb_colors = [pick_random() for _ in range(NUM_LEDS)]
        with lock:
            state['buf'] = build_multi_buf(
                [dim_pixel(c, dim) for c in pcb_colors])
        while not mode_stop.wait(CHASER_INTERVAL):
            dim = DIMMER_STEPS[dimmer_idx]
            # Shift colors right: [5]←[4]←[3]←[2]←[1]←[0]←new
            for i in range(NUM_LEDS - 1, 0, -1):
                pcb_colors[i] = pcb_colors[i - 1]
            pcb_colors[0] = pick_random()
            with lock:
                state['buf'] = build_multi_buf(
                    [dim_pixel(c, dim) for c in pcb_colors])

    # ── Animation: Theater Chase ──
    # Classic theater marquee: every 3rd LED is lit (Neutral White), the rest
    # are off. The lit position advances by one each THEATER_INTERVAL tick.
    # Pattern cycles: [1,0,0,1,0,0] → [0,1,0,0,1,0] → [0,0,1,0,0,1] → repeat
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
    # Each LED independently cycles through a pre-generated random color
    # sequence with fade-down-to-black, then fade-up-to-new-color transitions.
    # Staggered start delays make them appear to twinkle asynchronously.
    # Phases per LED: delay → fade_down → fade_up → hold → fade_down → ...
    def twinkle_loop():
        # Pre-generate color sequences and stagger delays at init
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
            pcb_delays.append(random.uniform(1.0, 4.0))  # Staggered start

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
    # Displays a repeating color pattern across the LED string (e.g., Christmas:
    # White-Green-Red-White-Green-Red). No animation — just holds the pattern.
    def preset_static_loop(pattern):
        pixels = [pattern[n % len(pattern)] for n in range(NUM_LEDS)]
        with lock:
            state['buf'] = build_multi_buf(pixels)
        # Hold pattern until mode_stop is set (check every 0.5s)
        while not mode_stop.wait(0.5):
            pass

    # ── Mode control ──
    # stop_mode() cleanly terminates any running animation thread.
    # start_mode() stops the current mode, then launches a new one.
    # All animation threads check mode_stop.is_set() to know when to exit.
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

    # ── Timer (2-hour / 4-hour auto power-off) ──
    # A background thread waits for the specified duration, then powers off
    # the LEDs (saving state for restore). Cancellable via timer_cancel Event.
    def cancel_timer():
        nonlocal timer_thread, timer_label
        if timer_thread:
            timer_cancel.set()
            timer_thread.join(timeout=1)
            timer_thread = None
            timer_label = None
        timer_cancel.clear()

    def timer_fire():
        """Called when timer expires — saves state and powers off."""
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
        """Start a new auto-off timer (cancels any existing one)."""
        nonlocal timer_thread, timer_label
        cancel_timer()
        timer_label = label

        def _wait():
            if not timer_cancel.wait(hours * 3600):
                timer_fire()
        timer_thread = threading.Thread(target=_wait, daemon=True)
        timer_thread.start()

    # ── Button handler ──
    # Central dispatch for all 25 buttons. Called from the RF receive loop
    # after debouncing. Modifies controller state and updates the LED buffer.
    def handle_button(btn_name):
        nonlocal power_on, saved_setting, current_wrgb, dimmer_idx
        nonlocal active_mode, modes_idx, white_idx
        nonlocal pending_slot, dual_color1, dual_color2

        # ─── Power (toggle on/off with state save/restore) ───
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

        # ─── Color buttons (15 direct colors) ───
        # If a pending_slot is active (Color1/Color2 was pressed before this),
        # the color goes into the dual-color slot instead of solid mode.
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

        # ─── Dimming (cycle brightness: 1.0 → 0.9 → ... → 0.1 → 1.0) ───
        # Only applies to solid, dual-color, and chaser modes.
        # Ignored during fade/strobe/theater/twinkle/preset (always full bright).
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

        # ─── Modes (cycle: Christmas → St Patrick's → 4th of July →
        #      Canada → Chaser → Theater Chase → Twinkle → wrap) ───
        # If already in a modes entry, advance to next. Otherwise start at 0.
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
    # Polls the nRF24L01+ RX FIFO at ~1000 Hz. For each received packet:
    #   1. Descramble (undo XN297L scramble + bit reversal)
    #   2. Filter out known background source packets
    #   3. Extract button code (meaningful bytes before idle tail)
    #   4. Debounce (ignore same code within DEBOUNCE_S window)
    #   5. Look up button name in hex_to_button map
    #   6. Dispatch to handle_button() or log as unknown
    try:
        while True:
            if radio.available():
                raw = radio.read()
                desc = descramble(raw)

                # Discard background source packets (CMD/IDLE headers)
                if is_background_packet(desc):
                    continue

                code = extract_button_code(desc)
                now = time.monotonic()

                # Debounce: skip if same button within 300ms
                if (code == last_button_code
                        and (now - last_button_time) < DEBOUNCE_S):
                    continue
                last_button_code = code
                last_button_time = now

                # Map hex code → button name and dispatch
                btn_name = hex_to_button.get(code)
                if btn_name:
                    handle_button(btn_name)
                else:
                    # Log unrecognized packets (useful during development)
                    print(f"  Unknown: {code[:48]}"
                          f"{'...' if len(code) > 48 else ''}")

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        # Clean shutdown: stop all threads, turn off LEDs, power down radio
        cancel_timer()
        mode_stop.set()
        state['running'] = False
        spi_thread.join(timeout=1)
        if mode_thread:
            mode_thread.join(timeout=1)
        # Send one final "all off" frame before closing SPI
        with lock:
            state['buf'] = build_solid_buf(None, 1.0)
        spi_led.xfer2(state['buf'][:])
        radio.power_down()
        spi_led.close()
        print("  Radio off, LEDs off. Goodbye.")


if __name__ == "__main__":
    main()
