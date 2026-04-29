#!/usr/bin/env python3
"""
LED controller for Enbrighten RGBW LED Cafe Lights via SPI1.

Drives TM1815B-based RGBW LEDs using raw SPI bit-banging on the
Raspberry Pi 5's hardware SPI1 peripheral.

The TM1815B is a Titan Micro single-wire LED driver (400 kHz variant
of the TM1814 family) with RGBW support and inverted signal polarity.

Protocol details (from WLED source + TM1814 datasheet):
    Bit rate:   400 kHz (2.5 µs per data bit)
    Signal:     Inverted — line idles HIGH, pulses go LOW
    Bit 1:      low ~625 ns,  high ~1875 ns
    Bit 0:      low ~1875 ns, high ~625 ns
    Reset:      HIGH >= 280 µs (line returns to idle-high)
    Color order: W, R, G, B  (white byte first)
    Frame:      C1, C2, D1, D2, ... Dn
                C1 = current-setting command (32 bits)
                C2 = bitwise complement of C1
                D1..Dn = pixel data (32 bits each: W,R,G,B)

SPI encoding at 1.6 MHz (~625 ns/SPI-bit), 4 SPI bits per data bit:
    Data 1 (inverted) -> 0b1000  (low 625 ns, high 1875 ns)
    Data 0 (inverted) -> 0b1110  (low 1875 ns, high 625 ns)

8 data bits x 4 SPI bits = 32 SPI bits = 4 SPI bytes per colour byte.

Hardware notes
--------------
* SPI bus 1 (GPIO 20 = SPI1 MOSI) keeps SPI0 free for the nRF24L01+.
* A bi-directional level shifter sits between GPIO 20 (3.3 V) and the
  LED data-in line (5 V).
* Colour order on the wire is **W R G B**.

Usage example:
    from led_controller import LEDStrip

    strip = LEDStrip(num_leds=12)
    strip.set_all(255, 0, 0, 0)   # all red
    strip.set_pixel(0, 0, 0, 0, 255)  # first pixel pure white
    strip.show()
    strip.clear()
"""

import time
import spidev


# SPI clock at 1.6 MHz gives ~625 ns per SPI bit.
# 4 SPI bits per data bit -> 2.5 µs per data bit = 400 kHz.
SPI_SPEED_HZ = 1_600_000

RESET_US = 300

# Default per-channel constant current (6-bit value, 0-63).
# 0 = 6.5 mA (minimum), 63 = maximum current.
# The TM1814 family uses C1/C2 commands to set drive current.
DEFAULT_CURRENT = 10


def _encode_byte_inverted(value):
    """Encode one byte into 4 SPI bytes using inverted TM1815B protocol.

    Inverted signal: line idles HIGH. A data '1' is a short LOW pulse
    followed by a long HIGH. A data '0' is a long LOW pulse followed
    by a short HIGH.

    At 1.6 MHz (625 ns/SPI-bit), 4 SPI bits per data bit:
        Data 1 -> 0b1000  (LOW 625 ns, HIGH 1875 ns)
        Data 0 -> 0b1110  (LOW 1875 ns, HIGH 625 ns)
    """
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b1000
        else:
            encoded = (encoded << 4) | 0b1110
    return [
        (encoded >> 24) & 0xFF,
        (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF,
        encoded & 0xFF,
    ]


# Pre-compute lookup table: colour value (0-255) -> 4-byte SPI pattern
_LUT = [bytes(_encode_byte_inverted(v)) for v in range(256)]

# Idle-high byte: line stays HIGH during reset
_IDLE_HIGH = 0xFF


def _build_c1c2(current_w, current_r, current_g, current_b):
    """Build the C1 and C2 current-setting command bytes.

    C1 is a 32-bit command encoding 6-bit current values for W, R, G, B
    with the top 2 bits of each byte set to 0.
    C2 is the bitwise complement of C1 (for validation by the chip).

    Returns (c1_bytes, c2_bytes) as 4-byte lists each.
    """
    c1_w = current_w & 0x3F
    c1_r = current_r & 0x3F
    c1_g = current_g & 0x3F
    c1_b = current_b & 0x3F
    c1 = bytes([c1_w, c1_r, c1_g, c1_b])
    c2 = bytes([b ^ 0xFF for b in c1])
    return c1, c2


class LEDStrip:
    """High-level driver for TM1815B RGBW LEDs over SPI1."""

    def __init__(self, num_leds, spi_bus=1, brightness=1.0,
                 current=DEFAULT_CURRENT):
        self.num_leds = num_leds
        self.brightness = max(0.0, min(1.0, brightness))

        self._spi = spidev.SpiDev()
        self._spi.open(spi_bus, 0)
        self._spi.max_speed_hz = SPI_SPEED_HZ
        self._spi.mode = 0b00

        self._c1, self._c2 = _build_c1c2(current, current, current, current)
        self._pixels = [(0, 0, 0, 0)] * num_leds

    # ----- pixel manipulation ------------------------------------------------

    def set_pixel(self, index, r, g, b, w=0):
        if 0 <= index < self.num_leds:
            self._pixels[index] = (r, g, b, w)

    def set_all(self, r, g, b, w=0):
        self._pixels = [(r, g, b, w)] * self.num_leds

    def set_pixel_tuple(self, index, rgbw):
        if rgbw and len(rgbw) == 4:
            self.set_pixel(index, *rgbw)

    def clear(self):
        self.set_all(0, 0, 0, 0)
        self.show()

    # ----- output -------------------------------------------------------------

    def show(self):
        """Push the internal pixel buffer to the physical LED strip.

        Frame format: C1 + C2 + D1 + D2 + ... + Dn
        Each encoded using inverted 4-SPI-bit-per-data-bit scheme.
        """
        buf = bytearray()

        # Encode C1 current-setting command
        for byte_val in self._c1:
            buf += _LUT[byte_val]

        # Encode C2 (complement of C1)
        for byte_val in self._c2:
            buf += _LUT[byte_val]

        # Encode pixel data in WRGB order
        br = self.brightness
        for r, g, b, w in self._pixels:
            adj_w = int(w * br)
            adj_r = int(r * br)
            adj_g = int(g * br)
            adj_b = int(b * br)
            buf += _LUT[adj_w] + _LUT[adj_r] + _LUT[adj_g] + _LUT[adj_b]

        self._spi.xfer2(list(buf))
        # Reset: line returns to idle-high for >= 280 µs
        time.sleep(RESET_US / 1_000_000)

    # ----- brightness ---------------------------------------------------------

    def set_brightness(self, level):
        self.brightness = max(0.0, min(1.0, level))

    # ----- convenience patterns -----------------------------------------------

    def fill_color(self, rgbw_tuple):
        if rgbw_tuple and len(rgbw_tuple) == 4:
            self.set_all(*rgbw_tuple)
            self.show()

    # ----- cleanup ------------------------------------------------------------

    def close(self):
        self._spi.close()


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    NUM_LEDS = 12

    print(f"Initialising {NUM_LEDS} TM1815B RGBW LEDs on SPI1 ...")
    strip = LEDStrip(num_leds=NUM_LEDS)

    colours = [
        ("Red",    (255, 0, 0, 0)),
        ("Green",  (0, 255, 0, 0)),
        ("Blue",   (0, 0, 255, 0)),
        ("White",  (0, 0, 0, 255)),
    ]

    try:
        for name, rgbw in colours:
            print(f"  {name} ...")
            strip.fill_color(rgbw)
            time.sleep(1)

        print("  All off.")
        strip.clear()

    except KeyboardInterrupt:
        strip.clear()
        print("\nInterrupted — LEDs off.")
    finally:
        strip.close()
