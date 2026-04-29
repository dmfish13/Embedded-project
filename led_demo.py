#!/usr/bin/env python3
"""
LED Cycling Demo for Enbrighten RGBW LED Cafe Lights.

Uses a direct-SPI driver for SK6812 RGBW LEDs (32 bits per pixel).
Drives LEDs via SPI1 MOSI (GPIO 20) through a level shifter.

SK6812 RGBW protocol (800 kHz, non-inverted):
    Line idles LOW.
    Data 1: HIGH ~833 ns, LOW ~417 ns
    Data 0: HIGH ~417 ns, LOW ~833 ns
    Color order: G, R, B, W
    Reset: LOW >= 80 µs

SPI encoding at 2.4 MHz, 3 SPI bits per data bit:
    Data 1 -> 0b110  (HIGH 833 ns, LOW 417 ns)
    Data 0 -> 0b100  (HIGH 417 ns, LOW 833 ns)
"""

import time
import random
import math
from spidev import SpiDev


# ---- SK6812 RGBW Direct SPI Driver ----

SPI_SPEED_HZ = 2_400_000


def _encode_byte(value):
    """Encode one colour byte into 3 SPI bytes for SK6812 protocol."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 3) | 0b110
        else:
            encoded = (encoded << 3) | 0b100
    return [
        (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF,
        encoded & 0xFF,
    ]


_LUT = [bytes(_encode_byte(v)) for v in range(256)]

_RESET_BYTES = b'\x00' * 30


class SK6812:
    """Drive SK6812 RGBW LEDs via SPI bit-banging at 2.4 MHz."""

    def __init__(self, num_leds, spi_bus=1, spi_device=0):
        self.num_leds = num_leds
        self._spi = SpiDev()
        self._spi.open(spi_bus, spi_device)
        self._spi.max_speed_hz = SPI_SPEED_HZ
        self._spi.mode = 0b00
        self._spi.lsbfirst = False
        self._pixels = [(0, 0, 0, 0)] * num_leds

    def set_pixel(self, index, r, g, b, w=0):
        if 0 <= index < self.num_leds:
            self._pixels[index] = (r, g, b, w)

    def set_all(self, r, g, b, w=0):
        self._pixels = [(r, g, b, w)] * self.num_leds

    def show(self):
        """Encode pixels in GRBW order and write to SPI."""
        buf = bytearray()

        for r, g, b, w in self._pixels:
            buf += _LUT[g] + _LUT[r] + _LUT[b] + _LUT[w]

        buf += _RESET_BYTES

        self._spi.xfer2(list(buf))

    def clear(self):
        self.set_all(0, 0, 0, 0)
        self.show()

    def close(self):
        self.clear()
        self._spi.close()


# ---- Configuration ----

NUM_LEDS = 12
strip = SK6812(NUM_LEDS, spi_bus=1, spi_device=0)


def set_all(r, g, b, w, brightness=1.0):
    strip.set_all(
        int(r * brightness),
        int(g * brightness),
        int(b * brightness),
        int(w * brightness)
    )
    strip.show()


def clear():
    strip.clear()


# ---- Color Definitions (R, G, B, W) ----

COLORS = {
    "Cool White":    (0,   0,   0,   255),
    "Daylight":      (20,  20,  40,  230),
    "Natural White": (10,  10,  10,  220),
    "Warm White":    (40,  20,  0,   200),
    "Vintage Amber": (80,  30,  0,   120),
    "Blue Prime":    (0,   0,   255, 0),
    "Green Prime":   (0,   255, 0,   0),
    "Red Prime":     (255, 0,   0,   0),
    "Mint":          (0,   200, 120, 0),
    "Yellow":        (255, 255, 0,   0),
}


def phase_1_color_cycle():
    """Cycle through 10 color settings, 5 seconds each."""
    print("\n--- Phase 1: Color Temperature & Prime Cycle ---\n")
    for name, (r, g, b, w) in COLORS.items():
        print(f"  {name:<16} RGBW=({r}, {g}, {b}, {w})")
        set_all(r, g, b, w)
        time.sleep(5)


def phase_2_brightness_test():
    """10-step brightness ramp-down on Yellow, 1 second per step."""
    print("\n--- Phase 2: 10-Step Brightness Test (Yellow) ---\n")
    r, g, b, w = COLORS["Yellow"]
    for step in range(10):
        brightness = 1.0 - (step * 0.1)
        pct = int(brightness * 100)
        print(f"  Brightness: {pct:>3}%")
        set_all(r, g, b, w, brightness=brightness)
        time.sleep(1)


def phase_3_fade():
    """Breathing fade on a random color for 7 seconds."""
    print("\n--- Phase 3: Fade (Breathing) Demo — 7 seconds ---\n")
    name = random.choice(list(COLORS.keys()))
    r, g, b, w = COLORS[name]
    print(f"  Fading: {name}  RGBW=({r}, {g}, {b}, {w})\n")

    start = time.monotonic()
    while time.monotonic() - start < 7.0:
        elapsed = time.monotonic() - start
        brightness = (math.sin(elapsed * math.pi) + 1.0) / 2.0
        set_all(r, g, b, w, brightness=brightness)
        time.sleep(0.03)


def phase_4_shutdown():
    """Turn off all LEDs."""
    print("\n--- Phase 4: Shutdown ---\n")
    strip.close()
    print("  All LEDs off. Done.")


def main():
    print("=" * 50)
    print("  Enbrighten RGBW LED Cycling Demo")
    print("=" * 50)

    print("\n  Starting in 5 seconds (LEDs off)...")
    clear()
    time.sleep(5)

    try:
        phase_1_color_cycle()
        phase_2_brightness_test()
        phase_3_fade()
        phase_4_shutdown()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        strip.close()
        print("  LEDs off.")


if __name__ == "__main__":
    main()
