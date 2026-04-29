#!/usr/bin/env python3
"""
LED Cycling Demo for Enbrighten RGBW LED Cafe Lights.

Drives TM1815B RGBW LEDs via SPI1 MOSI (GPIO 20) through a level shifter.

TM1815B protocol (400 kHz, return-to-zero):
    Line idles HIGH. Data encoded as LOW pulses.
    Logic 1: LOW 1300-2000 ns (typ 1440 ns)
    Logic 0: LOW 620-820 ns  (typ 720 ns)
    Color order: W, R, G, B
    Frame: C1, C2, D1, D2, ... Dn

SPI encoding at 1.6 MHz, 4 SPI bits per data bit:
    Logic 1 -> 0b0001  (LOW 1875 ns, HIGH 625 ns)
    Logic 0 -> 0b0111  (LOW 625 ns,  HIGH 1875 ns)
"""

import time
import random
import math
from spidev import SpiDev

SPI_SPEED_HZ = 1_600_000
DEFAULT_CURRENT = 10


def _encode_byte(value):
    """Encode one byte into 4 SPI bytes for TM1815B protocol."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001
        else:
            encoded = (encoded << 4) | 0b0111
    return [
        (encoded >> 24) & 0xFF,
        (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF,
        encoded & 0xFF,
    ]


_LUT = [bytes(_encode_byte(v)) for v in range(256)]


def _build_c1c2(current):
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([b ^ 0xFF for b in c1])
    return c1, c2


class TM1815B:
    """Drive TM1815B RGBW LEDs via SPI bit-banging at 1.6 MHz."""

    def __init__(self, num_leds, spi_bus=1, spi_device=0,
                 current=DEFAULT_CURRENT):
        self.num_leds = num_leds
        self._spi = SpiDev()
        self._spi.open(spi_bus, spi_device)
        self._spi.max_speed_hz = SPI_SPEED_HZ
        self._spi.mode = 0b00
        self._spi.lsbfirst = False
        self._c1, self._c2 = _build_c1c2(current)
        self._pixels = [(0, 0, 0, 0)] * num_leds

    def set_pixel(self, index, r, g, b, w=0):
        if 0 <= index < self.num_leds:
            self._pixels[index] = (r, g, b, w)

    def set_all(self, r, g, b, w=0):
        self._pixels = [(r, g, b, w)] * self.num_leds

    def show(self):
        """Encode C1+C2+pixels and write to SPI."""
        buf = bytearray(b'\xFF' * 80)

        for byte_val in self._c1:
            buf += _LUT[byte_val]
        for byte_val in self._c2:
            buf += _LUT[byte_val]

        for r, g, b, w in self._pixels:
            buf += _LUT[w] + _LUT[r] + _LUT[g] + _LUT[b]

        buf += b'\xFF' * 60

        self._spi.xfer2(list(buf))

    def clear(self):
        self.set_all(0, 0, 0, 0)
        self.show()

    def close(self):
        self.clear()
        self._spi.close()


NUM_LEDS = 4
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
    print("\n--- Phase 1: Color Temperature & Prime Cycle ---\n")
    for name, (r, g, b, w) in COLORS.items():
        print(f"  {name:<16} RGBW=({r}, {g}, {b}, {w})")
        set_all(r, g, b, w)
        time.sleep(5)


def phase_2_brightness_test():
    print("\n--- Phase 2: 10-Step Brightness Test (Yellow) ---\n")
    r, g, b, w = COLORS["Yellow"]
    for step in range(10):
        brightness = 1.0 - (step * 0.1)
        pct = int(brightness * 100)
        print(f"  Brightness: {pct:>3}%")
        set_all(r, g, b, w, brightness=brightness)
        time.sleep(1)


def phase_3_fade():
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
