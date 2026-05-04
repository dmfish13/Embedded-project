#!/usr/bin/env python3
"""
Promiscuous RF scanner — RF only, no LEDs.

nRF24L01+ in promiscuous mode (2-byte address, no CRC, 1 Mbps) captures
everything on channel 42. Detects button presses by finding duplicate
packets (remote retransmits same payload multiple times per press).

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
    print("  Detection: duplicate packets (remote retransmits per press)")
    print("=" * 70)
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24Promisc(spi, csn, ce)
    radio.configure(channel=42)
    print("  Radio: promiscuous mode on ch 42")
    print("  Listening... press buttons on the remote!")
    print()
    print(f"  {'Time':>8}  {'Presses':>7}  {'Pkts':>6}  {'Rate':>5}  Event")
    print("  " + "-" * 55)

    press_count = 0
    total_packets = 0
    start_time = time.monotonic()
    last_heartbeat = start_time
    heartbeat_interval = 3.0

    # Duplicate detection: keep recent packets in a sliding window
    recent_window = 0.5  # seconds
    recent_packets = []  # list of (time, bytes)
    last_trigger_time = 0
    debounce = 0.8
    dup_threshold = 2  # need 2+ identical packets to trigger

    try:
        while True:
            now = time.monotonic()

            if radio.available():
                raw = bytes(radio.read())
                total_packets += 1
                recent_packets.append((now, raw))

            # Prune old packets from window
            recent_packets = [(t, d) for t, d in recent_packets
                              if now - t < recent_window]

            # Check for duplicates in window
            if now - last_trigger_time > debounce and len(recent_packets) >= dup_threshold:
                # Count occurrences of each packet
                seen = {}
                for t, d in recent_packets:
                    seen[d] = seen.get(d, 0) + 1

                # Find any packet with 2+ occurrences
                dup_pkt = None
                for pkt, count in seen.items():
                    if count >= dup_threshold:
                        dup_pkt = pkt
                        break

                if dup_pkt is not None:
                    press_count += 1
                    last_trigger_time = now
                    elapsed = now - start_time
                    rate = total_packets / elapsed if elapsed > 0 else 0
                    ts = time.strftime("%H:%M:%S")
                    hex_str = "".join(f"{b:02X}" for b in dup_pkt[:24])
                    dup_count = seen[dup_pkt]
                    print(f"  {ts}  {press_count:>7}  {total_packets:>6}  "
                          f"{rate:>5.1f}  PRESS #{press_count} "
                          f"({dup_count} dupes)")
                    print(f"           payload: {hex_str}")
                    # Clear window after trigger to avoid re-triggering
                    recent_packets = []

            # Heartbeat
            if now - last_heartbeat >= heartbeat_interval:
                last_heartbeat = now
                elapsed = now - start_time
                rate = total_packets / elapsed if elapsed > 0 else 0
                ts = time.strftime("%H:%M:%S")
                win_count = len(recent_packets)
                print(f"  {ts}  {press_count:>7}  {total_packets:>6}  "
                      f"{rate:>5.1f}  . listening (win={win_count})")

    except KeyboardInterrupt:
        pass

    # Clean shutdown
    print()
    print()
    print("  Shutting down...")
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
    print(f"  Button presses:    {press_count}")
    print()
    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
