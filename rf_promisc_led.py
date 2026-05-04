#!/usr/bin/env python3
"""
Promiscuous RF sniffer with LED feedback.

nRF24L01+ in promiscuous mode (2-byte address, no CRC, 1 Mbps) captures
everything on channel 42. LED strip shows visual feedback for packet
bursts (button presses) vs silence.

LED output uses a continuous SPI thread (same method as
led_marquee_cycle_test.py) so the TM1815B stays latched reliably.

Promiscuous config:
  - SETUP_AW = 0x00 (2-byte address — widest net)
  - Pipe 0: address 0x00,0x55 (catches 0xAA preamble -> 0x55 sync)
  - Pipe 1: address 0x00,0xAA (catches 0x55 preamble -> 0xAA sync)
  - CRC disabled
  - Auto-ack disabled
  - 32-byte fixed payload
  - 1 Mbps, channel 42

Usage:
    python3 rf_scanner_reset.py
    python3 rf_promisc_led.py
"""

import time
import threading
from spidev import SpiDev
import board
import busio
import digitalio


# ═══════════════════════════════════════════════════════════════════════
#  LED strip (SPI1) — continuous output thread like led_marquee_cycle_test
# ═══════════════════════════════════════════════════════════════════════

NUM_LEDS = 6
C1 = [0x20, 0x20, 0x20, 0x20]
C2 = [0xDF, 0xDF, 0xDF, 0xDF]
LED_SPI_SPEED = 2_000_000
RESET = 80


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


def build_frame(pixels):
    buf = bytearray(b'\xFF' * RESET)
    for bv in C1:
        buf += LUT_4BIT[bv]
    for bv in C2:
        buf += LUT_4BIT[bv]
    for w, r, g, b in pixels:
        buf += LUT_4BIT[w] + LUT_4BIT[r] + LUT_4BIT[g] + LUT_4BIT[b]
    buf += b'\xFF' * RESET
    return buf


OFF = (0, 0, 0, 0)

COLORS = [
    ("White", (255, 0, 0, 0)),
    ("Red",   (0, 255, 0, 0)),
    ("Blue",  (0, 0, 0, 255)),
    ("Green", (0, 0, 255, 0)),
]


class LEDStrip:
    def __init__(self):
        self._spi = SpiDev()
        self._spi.open(1, 0)
        self._spi.max_speed_hz = LED_SPI_SPEED
        self._spi.mode = 0b00
        self._spi.lsbfirst = False
        self._lock = threading.Lock()
        self._buf = list(build_frame([OFF] * NUM_LEDS))
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            with self._lock:
                buf = self._buf[:]
            self._spi.xfer2(buf)

    def set_color(self, wrgb):
        frame = list(build_frame([wrgb] * NUM_LEDS))
        with self._lock:
            self._buf = frame

    def off(self):
        self.set_color(OFF)

    def close(self):
        self._running = False
        self._thread.join(timeout=2)
        self.off()
        time.sleep(0.05)
        self._spi.xfer2(list(build_frame([OFF] * NUM_LEDS)))
        self._spi.close()


# ═══════════════════════════════════════════════════════════════════════
#  nRF24L01+ promiscuous mode (SPI0)
# ═══════════════════════════════════════════════════════════════════════

class NRF24Promisc:
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

    def configure(self, channel=42):
        self._ce.value = False

        # CONFIG: PWR_UP=1, PRIM_RX=1, CRC disabled (EN_CRC=0)
        self._reg_write(0x00, 0x03)
        time.sleep(0.002)

        self._reg_write(0x01, 0x00)   # EN_AA: no auto-ack
        self._reg_write(0x02, 0x03)   # EN_RXADDR: pipes 0 and 1
        self._reg_write(0x03, 0x00)   # SETUP_AW: 2-byte address (illegal=widest)

        # Pipe 0: 0x00,0x55 — catches packets after 0xAA preamble
        self._reg_write_bytes(0x0A, b"\x00\x55")
        # Pipe 1: 0x00,0xAA — catches packets after 0x55 preamble
        self._reg_write_bytes(0x0B, b"\x00\xAA")

        self._reg_write(0x11, 32)     # RX_PW_P0: 32-byte payload
        self._reg_write(0x12, 32)     # RX_PW_P1: 32-byte payload
        self._reg_write(0x06, 0x07)   # RF_SETUP: 1 Mbps, 0 dBm
        self._reg_write(0x1C, 0x00)   # DYNPD: no dynamic payload
        self._reg_write(0x1D, 0x00)   # FEATURE: nothing

        self._cmd(0xE2)               # FLUSH_RX
        self._cmd(0xE1)               # FLUSH_TX
        self._reg_write(0x07, 0x70)   # STATUS: clear all flags

        self._reg_write(0x05, channel)
        self._ce.value = True

    def available(self):
        fifo = self._reg_read(0x17)
        return not bool(fifo & 0x01)

    def read(self):
        data = self._read_payload(32)
        self._reg_write(0x07, 0x70)
        return data

    def carrier_detect(self):
        return bool(self._reg_read(0x09) & 0x01)

    def power_down(self):
        self._ce.value = False
        self._reg_write(0x00, 0x00)


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  Promiscuous RF Sniffer + LED Feedback")
    print("  2-byte address | no CRC | 1 Mbps | channel 42")
    print("=" * 70)
    print()
    print("  LEDs start OFF (continuous SPI thread).")
    print("  Each detected button press cycles:")
    print("    OFF -> White -> Red -> Blue -> Green -> White -> ...")
    print()
    print("  Phase 1 (5s): Silence baseline — count noise packets")
    print("  Phase 2:      Listen — press triggers when burst > baseline")
    print()
    print("  Ctrl+C to quit.")
    print("=" * 70)

    # --- Init LEDs ---
    leds = LEDStrip()
    print("  LEDs: OFF (SPI1 continuous thread)")
    time.sleep(0.5)

    # --- Init RF ---
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24Promisc(spi, csn, ce)
    radio.configure(channel=42)
    print("  Radio: promiscuous mode on ch 42")

    # --- Phase 1: baseline noise measurement ---
    print("\n  Measuring noise baseline (5 seconds, don't press anything)...")
    baseline_start = time.monotonic()
    baseline_packets = 0
    while time.monotonic() - baseline_start < 5.0:
        if radio.available():
            radio.read()
            baseline_packets += 1

    noise_rate = baseline_packets / 5.0
    # Threshold: need at least 3x noise rate in a 0.5s window to trigger
    # Minimum threshold of 3 packets per window even if noise is zero
    trigger_threshold = max(int(noise_rate * 0.5 * 3), 3)

    print(f"  Noise: {baseline_packets} packets in 5s "
          f"({noise_rate:.1f}/s)")
    print(f"  Trigger: >{trigger_threshold} packets in 0.5s window")
    print()

    # --- Phase 2: detect button presses ---
    press_count = 0
    total_packets = 0
    color_name = "OFF"

    # Sliding window: count packets in last 0.5s
    window_size = 0.5
    window_packets = []
    last_trigger_time = 0
    debounce = 1.0

    try:
        print("  Listening... press buttons on the remote!")
        print(f"  {'Time':>8}  {'Presses':>7}  {'Color':<8}  "
              f"{'Win':>4}  {'Total':>6}  Note")
        print("  " + "-" * 60)

        while True:
            now = time.monotonic()

            if radio.available():
                raw = radio.read()
                total_packets += 1
                window_packets.append(now)

            # Prune old entries from window
            window_packets = [t for t in window_packets
                              if now - t < window_size]

            win_count = len(window_packets)

            # Trigger on burst above threshold, with debounce
            if (win_count >= trigger_threshold
                    and now - last_trigger_time > debounce):
                press_count += 1
                last_trigger_time = now
                color_idx = (press_count - 1) % len(COLORS)
                color_name, color_wrgb = COLORS[color_idx]
                leds.set_color(color_wrgb)

                ts = time.strftime("%H:%M:%S")
                print(f"  {ts}  {press_count:>7}  {color_name:<8}  "
                      f"{win_count:>4}  {total_packets:>6}  "
                      f"PRESS DETECTED")

                # Show a sample packet from this burst
                hex_str = "".join(f"{b:02X}" for b in raw[:20])
                print(f"           sample: {hex_str}")

            elif total_packets > 0 and total_packets % 500 == 0:
                ts = time.strftime("%H:%M:%S")
                print(f"  {ts}  {press_count:>7}  {color_name:<8}  "
                      f"{win_count:>4}  {total_packets:>6}  "
                      f"(background)")

    except KeyboardInterrupt:
        print()
        print()
        print("=" * 70)
        print("  RESULTS")
        print("=" * 70)
        print(f"  Total packets:     {total_packets}")
        print(f"  Noise rate:        {noise_rate:.1f}/s")
        print(f"  Trigger threshold: {trigger_threshold} in {window_size}s")
        print(f"  Button presses:    {press_count}")
    finally:
        try:
            radio.power_down()
        except Exception:
            pass
        leds.close()
        print("  Radio + LEDs shut down.")


if __name__ == "__main__":
    main()
