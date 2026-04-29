#!/usr/bin/env python3
"""
LED controller for Enbrighten RGBW LED Cafe Lights via SPI1.

Drives SK6812-compatible RGBW LEDs using SPI bit-banging on the
Raspberry Pi 5's hardware SPI1 peripheral.

Protocol (800 kHz, non-inverted, SK6812/WS2812B compatible):
    Line idles LOW.
    Data 1: HIGH ~833 ns, LOW ~417 ns
    Data 0: HIGH ~417 ns, LOW ~833 ns
    Reset:  LOW >= 80 µs
    Color order: G, R, B, W  (SK6812 RGBW standard)

SPI encoding at 2.4 MHz (~417 ns/SPI-bit), 3 SPI bits per data bit:
    Data 1 -> 0b110  (HIGH 833 ns, LOW 417 ns)
    Data 0 -> 0b100  (HIGH 417 ns, LOW 833 ns)

8 data bits × 3 SPI bits = 24 SPI bits = 3 SPI bytes per colour byte.

Hardware notes
--------------
* SPI bus 1 (GPIO 20 = SPI1 MOSI) keeps SPI0 free for the nRF24L01+.
* A bi-directional level shifter sits between GPIO 20 (3.3 V) and the
  LED data-in line (5 V).

Usage example:
    from led_controller import LEDStrip

    strip = LEDStrip(num_leds=12)
    strip.set_all(255, 0, 0, 0)   # all red
    strip.set_pixel(0, 0, 0, 0, 255)  # first pixel pure white
    strip.show()
    strip.clear()
"""

import spidev

SPI_SPEED_HZ = 2_400_000


def _encode_byte(value):
    """Encode one colour byte into 3 SPI bytes for SK6812 protocol.

    At 2.4 MHz (417 ns/SPI-bit), 3 SPI bits per data bit:
        Data 1 -> 0b110  (HIGH 833 ns, LOW 417 ns)
        Data 0 -> 0b100  (HIGH 417 ns, LOW 833 ns)
    """
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

# Reset: hold LOW for >= 80 µs.  At 2.4 MHz each byte is ~3.3 µs,
# so 30 zero-bytes ≈ 100 µs.
_RESET_BYTES = b'\x00' * 30


class LEDStrip:
    """High-level driver for SK6812 RGBW LEDs over SPI1."""

    def __init__(self, num_leds, spi_bus=1, brightness=1.0):
        self.num_leds = num_leds
        self.brightness = max(0.0, min(1.0, brightness))

        self._spi = spidev.SpiDev()
        self._spi.open(spi_bus, 0)
        self._spi.max_speed_hz = SPI_SPEED_HZ
        self._spi.mode = 0b00

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

        Encodes pixels in SK6812 GRBW order and appends a LOW reset
        period (>= 80 µs) so the chips latch the data.
        """
        buf = bytearray()

        br = self.brightness
        for r, g, b, w in self._pixels:
            adj_g = int(g * br)
            adj_r = int(r * br)
            adj_b = int(b * br)
            adj_w = int(w * br)
            buf += _LUT[adj_g] + _LUT[adj_r] + _LUT[adj_b] + _LUT[adj_w]

        buf += _RESET_BYTES

        self._spi.xfer2(list(buf))

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
    import time

    NUM_LEDS = 12

    print(f"Initialising {NUM_LEDS} SK6812 RGBW LEDs on SPI1 ...")
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
