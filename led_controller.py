#!/usr/bin/env python3
"""
LED controller for Enbrighten RGBW LED Cafe Lights via SPI1.

Drives TM1815B RGBW LEDs using SPI bit-banging on the Raspberry Pi 5's
hardware SPI1 peripheral.

Protocol (400 kHz, return-to-zero):
    Line idles HIGH. Data encoded as LOW pulses.
    Logic 1: LOW 1300-2000 ns (typ 1440 ns)
    Logic 0: LOW 620-820 ns  (typ 720 ns)
    Bit period: 2.5 µs
    Reset:  HIGH >= 200 µs
    Color order: W, R, G, B
    Frame: C1 + C2 + D1 + D2 + ... + Dn

SPI encoding at 1.6 MHz (~625 ns/SPI-bit), 4 SPI bits per data bit:
    Logic 1 -> 0b0001  (LOW 1875 ns, HIGH 625 ns)
    Logic 0 -> 0b0111  (LOW 625 ns,  HIGH 1875 ns)

8 data bits x 4 SPI bits = 32 SPI bits = 4 SPI bytes per colour byte.

Hardware notes
--------------
* SPI bus 1 (GPIO 20 = SPI1 MOSI) keeps SPI0 free for the nRF24L01+.
* A 3.3V-to-5V level shifter is required between GPIO 20 and the LED
  data-in line. TM1815B needs 5V logic levels.
* The on-board driver IC must be disconnected so the Pi is the sole
  data source on the LED chain.

Usage example:
    from led_controller import LEDStrip

    strip = LEDStrip(num_leds=4)
    strip.set_all(255, 0, 0, 0)   # all red
    strip.set_pixel(0, 0, 0, 0, 255)  # first pixel pure white
    strip.show()
    strip.clear()
"""

import spidev

SPI_SPEED_HZ = 1_600_000
DEFAULT_CURRENT = 10


def _encode_byte(value):
    """Encode one byte into 4 SPI bytes for TM1815B protocol.

    At 1.6 MHz (625 ns/SPI-bit), 4 SPI bits per data bit:
        Logic 1 -> 0b0001  (LOW 1875 ns, HIGH 625 ns)  T1l: 1300-2000 ns
        Logic 0 -> 0b0111  (LOW 625 ns,  HIGH 1875 ns) T0l: 620-820 ns
    """
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


def _build_c1c2(current_w, current_r, current_g, current_b):
    """Build C1 and C2 current-setting command bytes.

    C1 encodes 6-bit current values for W, R, G, B.
    C2 is the bitwise complement of C1.
    """
    c1 = bytes([current_w & 0x3F, current_r & 0x3F,
                current_g & 0x3F, current_b & 0x3F])
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

    def show(self):
        """Push pixel buffer to the LED strip.

        Leading 0xFF bytes establish idle-HIGH reset (>= 200 µs).
        Trailing 0xFF bytes hold HIGH for the latch/reset period.
        """
        buf = bytearray(b'\xFF' * 80)

        for byte_val in self._c1:
            buf += _LUT[byte_val]
        for byte_val in self._c2:
            buf += _LUT[byte_val]

        br = self.brightness
        for r, g, b, w in self._pixels:
            buf += (_LUT[int(w * br)] + _LUT[int(r * br)] +
                    _LUT[int(g * br)] + _LUT[int(b * br)])

        buf += b'\xFF' * 60

        self._spi.xfer2(list(buf))

    def set_brightness(self, level):
        self.brightness = max(0.0, min(1.0, level))

    def fill_color(self, rgbw_tuple):
        if rgbw_tuple and len(rgbw_tuple) == 4:
            self.set_all(*rgbw_tuple)
            self.show()

    def close(self):
        self._spi.close()


if __name__ == "__main__":
    import time

    NUM_LEDS = 4

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
