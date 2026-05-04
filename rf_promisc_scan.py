#!/usr/bin/env python3
"""
Promiscuous RF scanner — RF only, no LEDs.

nRF24L01+ in promiscuous mode (2-byte address, no CRC, 1 Mbps) captures
everything on channel 42. Detects button presses as packet-rate spikes
above the rolling average.

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

        self._reg_write_bytes(0x0A, b"\x00\x55")
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
    print("  Promiscuous RF Scanner")
    print("  2-byte address | no CRC | 1 Mbps | channel 42")
    print("  Detection: adaptive rate spike (rolling avg + margin)")
    print("=" * 70)
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24Promisc(spi, csn, ce)
    radio.configure(channel=42)
    print("  Radio: promiscuous mode on ch 42")

    # --- Baseline: 3 seconds ---
    print("  Measuring baseline (3s, don't press anything)...")
    baseline_start = time.monotonic()
    baseline_packets = 0
    while time.monotonic() - baseline_start < 3.0:
        if radio.available():
            radio.read()
            baseline_packets += 1

    noise_rate = baseline_packets / 3.0
    print(f"  Baseline: {baseline_packets} packets in 3s ({noise_rate:.1f}/s)")
    print()
    print("  Listening... press buttons on the remote!")
    print(f"  {'Time':>8}  {'Presses':>7}  {'Pkts':>6}  {'Win':>4}  {'Avg':>4}  Event")
    print("  " + "-" * 60)

    press_count = 0
    total_packets = 0
    start_time = time.monotonic()
    last_heartbeat = start_time
    heartbeat_interval = 3.0

    # Sliding window for current rate
    window_size = 0.3  # shorter window = more responsive
    window_times = []

    # Rolling average: track window counts over last 5 seconds
    avg_history = []  # list of (time, window_count)
    avg_window = 5.0

    # Trigger settings
    margin = 10  # trigger when win_count > rolling_avg + margin
    last_trigger_time = 0
    debounce = 0.8

    last_sample = None

    try:
        while True:
            now = time.monotonic()

            if radio.available():
                raw = radio.read()
                total_packets += 1
                window_times.append(now)
                last_sample = raw

            # Prune window
            window_times = [t for t in window_times if now - t < window_size]
            win_count = len(window_times)

            # Update rolling average every 0.1s
            avg_history.append((now, win_count))
            avg_history = [(t, c) for t, c in avg_history
                           if now - t < avg_window]

            # Compute rolling average
            if len(avg_history) > 10:
                rolling_avg = sum(c for _, c in avg_history) / len(avg_history)
            else:
                rolling_avg = noise_rate * window_size

            # Trigger on spike above rolling average
            if (win_count > rolling_avg + margin
                    and now - last_trigger_time > debounce):
                press_count += 1
                last_trigger_time = now
                elapsed = now - start_time
                ts = time.strftime("%H:%M:%S")
                print(f"  {ts}  {press_count:>7}  {total_packets:>6}  "
                      f"{win_count:>4}  {rolling_avg:>4.0f}  "
                      f"PRESS #{press_count}")
                if last_sample:
                    hex_str = "".join(f"{b:02X}" for b in last_sample[:24])
                    print(f"           payload: {hex_str}")

            # Heartbeat
            if now - last_heartbeat >= heartbeat_interval:
                last_heartbeat = now
                elapsed = now - start_time
                rate = total_packets / elapsed if elapsed > 0 else 0
                ts = time.strftime("%H:%M:%S")
                print(f"  {ts}  {press_count:>7}  {total_packets:>6}  "
                      f"{win_count:>4}  {rolling_avg:>4.0f}  "
                      f". ({rate:.0f}/s)")

    except KeyboardInterrupt:
        pass

    # Clean shutdown
    print()
    print()
    print("  Shutting down radio...")
    try:
        radio.power_down()
    except Exception:
        pass

    elapsed = time.monotonic() - start_time
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)
    print(f"  Duration:          {elapsed:.1f}s")
    print(f"  Total packets:     {total_packets}")
    print(f"  Avg rate:          {total_packets/elapsed:.1f}/s" if elapsed > 0 else "")
    print(f"  Noise baseline:    {noise_rate:.1f}/s")
    print(f"  Button presses:    {press_count}")
    print()
    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
