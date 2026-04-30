#!/usr/bin/env python3
"""
LED controller for Enbrighten RGBW LED Cafe Lights via SPI1.

Uses the rpi5-ws2812 library, which drives WS2812 / SK6812-compatible
LEDs through the Raspberry Pi 5's hardware SPI peripheral.

Hardware notes
--------------
* SPI bus 1 is used (GPIO 20 = SPI1 MOSI) to keep SPI0 free for the
  nRF24L01+ radio.
* An Adafruit bi-directional level shifter sits between Pi GPIO 20 (3.3 V)
  and the LED data-in line (5 V).
* The Enbrighten LEDs are SK6812-RGBW (4 colour channels per pixel).
  The rpi5-ws2812 library must be told to use **GRBW** colour order
  so the White channel is included.

Usage example:
    from led_controller import LEDStrip

    strip = LEDStrip(num_leds=12)
    strip.set_all(255, 0, 0, 0)   # all red
    strip.set_pixel(0, 0, 0, 0, 255)  # first pixel pure white
    strip.show()
    strip.clear()
"""

from rpi5_ws2812 import ws2812


class LEDStrip:
    """High-level wrapper around rpi5-ws2812 for RGBW LED control."""

    def __init__(self, num_leds, spi_bus=1, brightness=1.0):
        """Initialise the LED strip.

        Args:
            num_leds:   Number of RGBW LEDs in the string.
            spi_bus:    SPI bus number (default 1 to avoid conflicting
                        with the nRF24L01+ on SPI0).
            brightness: Global brightness multiplier (0.0 – 1.0).
        """
        self.num_leds = num_leds
        self.brightness = max(0.0, min(1.0, brightness))

        # Initialise the strip on SPI1 with GRBW colour order for RGBW LEDs.
        # rpi5-ws2812 expects: ws2812(num_leds, spi_bus, color_order)
        self.strip = ws2812(num_leds, spi_bus, "GRBW")

        # Internal pixel buffer: list of (R, G, B, W) tuples
        self._pixels = [(0, 0, 0, 0)] * num_leds

    # ----- pixel manipulation ------------------------------------------------

    def set_pixel(self, index, r, g, b, w=0):
        """Set a single pixel to the given RGBW colour.

        Args:
            index: Pixel position (0-based).
            r, g, b, w: Colour channel values (0-255).
        """
        if 0 <= index < self.num_leds:
            self._pixels[index] = (r, g, b, w)

    def set_all(self, r, g, b, w=0):
        """Set every pixel to the same RGBW colour."""
        self._pixels = [(r, g, b, w)] * self.num_leds

    def set_pixel_tuple(self, index, rgbw):
        """Set a single pixel from an (R, G, B, W) tuple."""
        if rgbw and len(rgbw) == 4:
            self.set_pixel(index, *rgbw)

    def clear(self):
        """Turn off all LEDs (sets every channel to 0) and push to strip."""
        self.set_all(0, 0, 0, 0)
        self.show()

    # ----- output -------------------------------------------------------------

    def show(self):
        """Push the internal pixel buffer to the physical LED strip."""
        for i, (r, g, b, w) in enumerate(self._pixels):
            # Apply brightness scaling
            br = self.brightness
            adj_r = int(r * br)
            adj_g = int(g * br)
            adj_b = int(b * br)
            adj_w = int(w * br)
            # rpi5-ws2812 set_pixel: (index, red, green, blue, white)
            self.strip.set_pixel(i, adj_r, adj_g, adj_b, adj_w)
        self.strip.show()

    # ----- brightness ---------------------------------------------------------

    def set_brightness(self, level):
        """Set global brightness (0.0 – 1.0). Call show() to apply."""
        self.brightness = max(0.0, min(1.0, level))

    # ----- convenience patterns -----------------------------------------------

    def fill_color(self, rgbw_tuple):
        """Fill the entire strip with an RGBW tuple and show immediately."""
        if rgbw_tuple and len(rgbw_tuple) == 4:
            self.set_all(*rgbw_tuple)
            self.show()


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import time

    NUM_LEDS = 12  # adjust to match your string length

    print(f"Initialising {NUM_LEDS} RGBW LEDs on SPI1 ...")
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
