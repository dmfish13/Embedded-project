#!/usr/bin/env python3
"""
LED Cycling Demo for Enbrighten RGBW LED Cafe Lights.

Uses a direct-SPI driver for TM1815B RGBW LEDs (32 bits per pixel).
Drives LEDs via SPI1 MOSI (GPIO 20) through a level shifter.

TM1815B timing (single-wire NRZ):
    Bit 1:  high ~600 ns, low ~300 ns
    Bit 0:  high ~300 ns, low ~600 ns
    Reset:  low >= 280 µs

At 2.4 MHz SPI clock (~417 ns/bit), 3 SPI bits per data bit:
    Data 1 -> 0b110  (high ~833 ns, low ~417 ns)
    Data 0 -> 0b100  (high ~417 ns, low ~833 ns)
"""

import time
import random
import math
from spidev import SpiDev


# ---- TM1815B RGBW Direct SPI Driver ----

# Pre-compute: each colour value (0-255) -> 3 SPI bytes (24 SPI bits)
def _build_lut():
    lut = []
    for val in range(256):
        encoded = 0
        for bit_pos in range(7, -1, -1):
            if val & (1 << bit_pos):
                encoded = (encoded << 3) | 0b110
            else:
                encoded = (encoded << 3) | 0b100
        lut.append(bytes([
            (encoded >> 16) & 0xFF,
            (encoded >> 8) & 0xFF,
            encoded & 0xFF,
        ]))
    return lut

_LUT = _build_lut()


class TM1815B:
    """Drive TM1815B RGBW LEDs via SPI bit-banging at 2.4 MHz."""

    RESET_US = 300  # >= 280 µs low for reset

    def __init__(self, num_leds, spi_bus=1, spi_device=0):
        self.num_leds = num_leds
        self._spi = SpiDev()
        self._spi.open(spi_bus, spi_device)
        self._spi.max_speed_hz = 2_400_000
        self._spi.mode = 0b00
        self._spi.lsbfirst = False
        self._pixels = [(0, 0, 0, 0)] * num_leds

    def set_pixel(self, index, r, g, b, w=0):
        if 0 <= index < self.num_leds:
            self._pixels[index] = (r, g, b, w)

    def set_all(self, r, g, b, w=0):
        self._pixels = [(r, g, b, w)] * self.num_leds

    def show(self):
        """Encode all pixels and write to SPI."""
        buf = bytearray()
        for r, g, b, w in self._pixels:
            # TM1815B RGBW wire order: Green, Red, Blue, White
            buf += _LUT[g] + _LUT[r] + _LUT[b] + _LUT[w]
        self._spi.xfer2(list(buf))
        time.sleep(self.RESET_US / 1_000_000)

    def clear(self):
        self.set_all(0, 0, 0, 0)
        self.show()

    def close(self):
        self.clear()
        self._spi.close()


# ---- Configuration ----

NUM_LEDS = 12
strip = TM1815B(NUM_LEDS, spi_bus=1, spi_device=0)


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
