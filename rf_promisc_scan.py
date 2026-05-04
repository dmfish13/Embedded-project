#!/usr/bin/env python3
"""
Promiscuous RF scanner — RF only, no LEDs.

nRF24L01+ in promiscuous mode (2-byte address, no CRC, 1 Mbps) captures
everything on channel 42. Detects button presses as packet bursts above
the noise baseline.

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
    python3 rf_promisc_scan.py
"""

import time
import board
import busio
import digitalio


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

        self._reg_write(0x00, 0x03)    # CONFIG: PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)

        self._reg_write(0x01, 0x00)    # EN_AA: no auto-ack
        self._reg_write(0x02, 0x03)    # EN_RXADDR: pipes 0 and 1
        self._reg_write(0x03, 0x00)    # SETUP_AW: 2-byte address (widest)

        # Pipe 0: catches packets after 0xAA preamble
        self._reg_write_bytes(0x0A, b"\x00\x55")
        # Pipe 1: catches packets after 0x55 preamble
        self._reg_write_bytes(0x0B, b"\x00\xAA")

        self._reg_write(0x11, 32)      # RX_PW_P0: 32-byte payload
        self._reg_write(0x12, 32)      # RX_PW_P1: 32-byte payload
        self._reg_write(0x06, 0x07)    # RF_SETUP: 1 Mbps, 0 dBm
        self._reg_write(0x1C, 0x00)    # DYNPD: off
        self._reg_write(0x1D, 0x00)    # FEATURE: off

        self._cmd(0xE2)                # FLUSH_RX
        self._cmd(0xE1)                # FLUSH_TX
        self._reg_write(0x07, 0x70)    # STATUS: clear flags

        self._reg_write(0x05, channel)
        self._ce.value = True

    def available(self):
        fifo = self._reg_read(0x17)
        return not bool(fifo & 0x01)

    def read(self):
        data = self._read_payload(32)
        self._reg_write(0x07, 0x70)
        return data

    def power_down(self):
        self._ce.value = False
        self._reg_write(0x00, 0x00)


def main():
    print("=" * 70)
    print("  Promiscuous RF Scanner (no LEDs)")
    print("  2-byte address | no CRC | 1 Mbps | channel 42")
    print("=" * 70)
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24Promisc(spi, csn, ce)
    radio.configure(channel=42)
    print("  Radio: promiscuous mode on ch 42")

    # --- Phase 1: noise baseline ---
    print("\n  Measuring noise baseline (5 seconds, don't press anything)...")
    baseline_start = time.monotonic()
    baseline_packets = 0
    baseline_samples = []
    while time.monotonic() - baseline_start < 5.0:
        if radio.available():
            raw = radio.read()
            baseline_packets += 1
            if baseline_packets <= 5:
                baseline_samples.append(raw)

    noise_rate = baseline_packets / 5.0
    trigger_threshold = max(int(noise_rate * 0.5 * 3), 3)

    print(f"  Noise: {baseline_packets} packets in 5s ({noise_rate:.1f}/s)")
    print(f"  Trigger: >{trigger_threshold} packets in 0.5s window")
    if baseline_samples:
        print(f"  Sample noise packets:")
        for i, s in enumerate(baseline_samples):
            print(f"    [{i}] {''.join(f'{b:02X}' for b in s[:24])}")
    print()

    # --- Phase 2: detect presses ---
    press_count = 0
    total_packets = 0
    window_size = 0.5
    window_packets = []
    last_trigger_time = 0
    debounce = 1.0

    try:
        print("  Listening... press buttons on the remote!")
        print(f"  {'Time':>8}  {'Presses':>7}  {'Win':>4}  "
              f"{'Total':>6}  {'Rate/s':>6}  Note")
        print("  " + "-" * 60)

        phase2_start = time.monotonic()

        while True:
            now = time.monotonic()

            if radio.available():
                raw = radio.read()
                total_packets += 1
                window_packets.append((now, raw))

            window_packets = [(t, d) for t, d in window_packets
                              if now - t < window_size]

            win_count = len(window_packets)

            if (win_count >= trigger_threshold
                    and now - last_trigger_time > debounce):
                press_count += 1
                last_trigger_time = now

                ts = time.strftime("%H:%M:%S")
                elapsed = now - phase2_start
                rate = total_packets / elapsed if elapsed > 0 else 0
                print(f"  {ts}  {press_count:>7}  {win_count:>4}  "
                      f"{total_packets:>6}  {rate:>6.1f}  "
                      f"PRESS DETECTED")

                # Show sample packets from this burst
                burst = window_packets[-min(3, len(window_packets)):]
                for i, (t, d) in enumerate(burst):
                    hex_str = "".join(f"{b:02X}" for b in d[:24])
                    print(f"           burst[{i}]: {hex_str}")

            elif total_packets > 0 and total_packets % 500 == 0:
                ts = time.strftime("%H:%M:%S")
                elapsed = now - phase2_start
                rate = total_packets / elapsed if elapsed > 0 else 0
                print(f"  {ts}  {press_count:>7}  {win_count:>4}  "
                      f"{total_packets:>6}  {rate:>6.1f}  "
                      f"(background)")

    except KeyboardInterrupt:
        elapsed = time.monotonic() - phase2_start
        print()
        print()
        print("=" * 70)
        print("  RESULTS")
        print("=" * 70)
        print(f"  Duration:          {elapsed:.1f}s")
        print(f"  Total packets:     {total_packets}")
        print(f"  Avg rate:          {total_packets/elapsed:.1f}/s" if elapsed > 0 else "")
        print(f"  Noise baseline:    {noise_rate:.1f}/s")
        print(f"  Trigger threshold: {trigger_threshold} in {window_size}s")
        print(f"  Button presses:    {press_count}")
    finally:
        try:
            radio.power_down()
        except Exception:
            pass
        print("  Radio shut down.")


if __name__ == "__main__":
    main()
