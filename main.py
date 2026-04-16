#!/usr/bin/env python3
"""
Enbrighten LED Cafe Lights — Raspberry Pi 5 Controller

Dual-bus architecture:
  SPI0 → nRF24L01+ radio (receive commands from Jasco QOBRGBXYZA remote)
  SPI1 → RGBW LED string  (drive SK6812-type Enbrighten LEDs via level shifter)

This script ties the RF scanner and LED controller together:
  1. Listens for RF packets from the remote on SPI0.
  2. Matches captured payloads against the button map.
  3. Drives the LEDs on SPI1 accordingly.

Until the real RF hex payloads are captured (via rf_scanner.py) and entered
into button_map.py, this script will log received packets and not yet match
them to button actions.
"""

import time
import sys

import board
import digitalio
from circuitpython_nrf24l01.rf24 import RF24

from button_map import BUTTON_MAP, lookup_by_hex
from led_controller import LEDStrip


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NUM_LEDS = 12           # number of RGBW LEDs in the Enbrighten string
LISTEN_CHANNEL = None   # set to a fixed channel once the remote's freq is found
BRIGHTNESS = 0.8        # default brightness (0.0 – 1.0)


# ---------------------------------------------------------------------------
# Radio setup (SPI0)
# ---------------------------------------------------------------------------
ce_pin = digitalio.DigitalInOut(board.D25)
csn_pin = digitalio.DigitalInOut(board.D8)
spi_radio = board.SPI()
nrf = RF24(spi_radio, csn_pin, ce_pin)


def configure_radio(channel=None):
    """Configure the radio identically to rf_scanner.py."""
    nrf.crc = 0
    nrf.auto_ack = False
    nrf.address_length = 2
    nrf.open_rx_pipe(0, b"\x00\x55")
    nrf.open_rx_pipe(1, b"\x00\xAA")
    nrf.payload_length = 32
    nrf.data_rate = 1
    nrf.pa_level = -12
    if channel is not None:
        nrf.channel = channel
    nrf.listen = True


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class LightState:
    """Tracks the current state of the LED strip."""

    def __init__(self, strip):
        self.strip = strip
        self.power_on = True
        self.current_color = (0, 0, 0, 255)  # start on white
        self.brightness = BRIGHTNESS

        self.strip.set_brightness(self.brightness)
        self.strip.fill_color(self.current_color)

    def handle_button(self, button_key):
        """React to a recognised button press."""
        if button_key == "Power":
            self.power_on = not self.power_on
            if self.power_on:
                self.strip.set_brightness(self.brightness)
                self.strip.fill_color(self.current_color)
                print("  -> Power ON")
            else:
                self.strip.clear()
                print("  -> Power OFF")
            return

        # Ignore other buttons while powered off
        if not self.power_on:
            print("  -> (power is off — ignoring)")
            return

        if button_key == "Dimming":
            # Cycle brightness: 100% → 75% → 50% → 25% → 100%
            levels = [1.0, 0.75, 0.5, 0.25]
            try:
                idx = levels.index(self.brightness)
                self.brightness = levels[(idx + 1) % len(levels)]
            except ValueError:
                self.brightness = 1.0
            self.strip.set_brightness(self.brightness)
            self.strip.show()
            print(f"  -> Brightness {int(self.brightness * 100)}%")
            return

        # Color buttons
        btn = BUTTON_MAP.get(button_key)
        if btn and btn["rgbw"] is not None:
            self.current_color = btn["rgbw"]
            self.strip.fill_color(self.current_color)
            print(f"  -> Color: {btn['label']}  RGBW={self.current_color}")
            return

        print(f"  -> Button '{button_key}' acknowledged (no handler yet)")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("  Enbrighten LED Controller — Raspberry Pi 5")
    print("  SPI0 = nRF24L01+ radio  |  SPI1 = RGBW LED string")
    print("=" * 70)

    # Initialise LED strip on SPI1
    print(f"\nInitialising {NUM_LEDS} RGBW LEDs on SPI1 ...")
    strip = LEDStrip(num_leds=NUM_LEDS, spi_bus=1, brightness=BRIGHTNESS)
    state = LightState(strip)

    # Initialise radio on SPI0
    print("Configuring nRF24L01+ on SPI0 ...")
    configure_radio(channel=LISTEN_CHANNEL)

    if LISTEN_CHANNEL is not None:
        print(f"Listening on channel {LISTEN_CHANNEL} "
              f"({2400 + LISTEN_CHANNEL} MHz)")
    else:
        print("No fixed channel set — sweeping all channels.")
        print("Run rf_scanner.py first to find the remote's channel,")
        print("then set LISTEN_CHANNEL in this script.\n")

    print("Waiting for remote button presses ... (Ctrl+C to quit)\n")

    packet_count = 0
    channel_idx = 0

    try:
        while True:
            # If no fixed channel, sweep manually
            if LISTEN_CHANNEL is None:
                nrf.listen = False
                nrf.channel = channel_idx
                nrf.listen = True
                channel_idx = (channel_idx + 1) % 126

            if nrf.available():
                raw = nrf.read()
                packet_count += 1
                hex_str = " ".join(f"{b:02X}" for b in raw)
                hex_compact = "".join(f"{b:02X}" for b in raw)

                ts = time.strftime("%H:%M:%S")
                ch = nrf.channel
                print(f"[{ts}]  pkt #{packet_count}  ch={ch}  {hex_str}")

                # Try to match against known button payloads
                match = lookup_by_hex(hex_compact)
                if match:
                    btn_key = None
                    for k, v in BUTTON_MAP.items():
                        if v is match:
                            btn_key = k
                            break
                    if btn_key:
                        print(f"  >> Matched: {match['label']}")
                        state.handle_button(btn_key)
                else:
                    print("  >> (no button match — run rf_scanner.py "
                          "to capture hex codes)")

            # Small delay to prevent busy-spinning
            time.sleep(0.001)

    except KeyboardInterrupt:
        print(f"\n\nShutting down. Packets received: {packet_count}")
        strip.clear()
        nrf.listen = False
        nrf.power = False
        print("LEDs off, radio powered down. Goodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()
