#!/usr/bin/env python3
"""
Sync Word Finder for the RF remote.

Captures raw packets on channels 21 and 64 using multiple candidate
address patterns, then performs statistical analysis to find byte
positions or multi-byte sequences that repeat consistently, which
would reveal the remote's actual sync/address word.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_sync_finder.py
"""

import time
import board
import busio
import digitalio
from collections import Counter


class NRF24L01:
    """Minimal direct-SPI driver for the nRF24L01+."""

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

    def _read_reg(self, reg):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        buf = bytearray([reg, 0xFF])
        self._spi.write_readinto(buf, buf)
        self._csn.value = True
        self._spi.unlock()
        return buf[1]

    def _write_reg(self, reg, value):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg, value]))
        self._csn.value = True
        self._spi.unlock()

    def _write_reg_bytes(self, reg, data):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg]) + bytearray(data))
        self._csn.value = True
        self._spi.unlock()

    def _command(self, cmd):
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

    def configure(self, channel, addr):
        """Configure the radio on a specific channel with a given 2-byte address.

        Args:
            channel: RF channel number (0-125)
            addr: 2-byte address as bytes (e.g. b"\\x00\\x55")
        """
        self._ce.value = False
        self._write_reg(0x00, 0x03)   # CONFIG: PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)   # EN_AA: disabled
        self._write_reg(0x02, 0x01)   # EN_RXADDR: pipe 0 only
        self._write_reg(0x03, 0x00)   # SETUP_AW: 2-byte address
        self._write_reg_bytes(0x0A, addr)  # RX_ADDR_P0
        self._write_reg(0x11, 32)     # RX_PW_P0: 32-byte payload
        self._write_reg(0x06, 0x07)   # RF_SETUP: 1 Mbps, 0 dBm
        self._write_reg(0x1C, 0x00)   # DYNPD: disabled
        self._write_reg(0x1D, 0x00)   # FEATURE: disabled
        self._command(0xE2)           # FLUSH_RX
        self._command(0xE1)           # FLUSH_TX
        self._write_reg(0x07, 0x70)   # STATUS: clear flags
        self._write_reg(0x05, channel)  # RF_CH
        self._ce.value = True

    def available(self):
        fifo = self._read_reg(0x17)
        return not bool(fifo & 0x01)

    def read(self):
        data = self._read_payload(32)
        self._write_reg(0x07, 0x40)
        return data

    def power_down(self):
        self._ce.value = False
        self._write_reg(0x00, 0x00)


def find_common_bytes(packets):
    """Analyze byte positions for values that repeat across many packets.

    A high repetition rate at a specific position suggests the remote's
    sync word is aligned at that offset.
    """
    if not packets:
        return
    print(f"\n  Analyzing {len(packets)} packets for common bytes...\n")
    print(f"  {'Pos':>4}  {'Most Common':>12}  {'Count':>6}  {'Pct':>5}")
    print(f"  {'-'*4}  {'-'*12}  {'-'*6}  {'-'*5}")

    for pos in range(min(16, len(packets[0]))):
        values = [p[pos] for p in packets]
        counter = Counter(values)
        most_common_val, most_common_count = counter.most_common(1)[0]
        pct = most_common_count / len(packets) * 100
        marker = " <<<" if pct > 30 else ""
        print(f"  {pos:>4}  0x{most_common_val:02X} ({most_common_val:08b})  "
              f"{most_common_count:>6}  {pct:>4.0f}%{marker}")


def find_common_ngrams(packets, n=3):
    """Search for repeating n-byte sequences across all packets.

    Recurring multi-byte patterns would indicate a consistent data
    structure within the transmissions.
    """
    ngram_counter = Counter()
    for pkt in packets:
        for i in range(len(pkt) - n + 1):
            ngram = tuple(pkt[i:i+n])
            ngram_counter[ngram] += 1

    print(f"\n  Most common {n}-byte sequences:")
    print(f"  {'Hex':<20}  {'Count':>6}")
    print(f"  {'-'*20}  {'-'*6}")
    for ngram, count in ngram_counter.most_common(15):
        hex_str = " ".join(f"{b:02X}" for b in ngram)
        pct = count / len(packets) * 100
        print(f"  {hex_str:<20}  {count:>6}  ({pct:.0f}%)")


def main():
    print("=" * 60)
    print("  Sync Word Finder")
    print("  Captures packets on ch 21 and ch 64")
    print("=" * 60)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    # Try multiple 2-byte address patterns
    addresses = [
        b"\x00\x55",
        b"\x00\xAA",
        b"\x55\x55",
        b"\xAA\xAA",
        b"\x55\xAA",
        b"\xAA\x55",
    ]

    for channel in [21, 64]:
        print(f"\n{'='*60}")
        print(f"  Channel {channel} ({2400+channel} MHz)")
        print(f"{'='*60}")

        for addr in addresses:
            addr_hex = " ".join(f"{b:02X}" for b in addr)
            print(f"\n  Address: {addr_hex}")
            print(f"  HOLD any button on the remote.")
            input(f"  Press Enter to capture 10 seconds...")

            radio.configure(channel, addr)
            packets = []
            deadline = time.monotonic() + 10
            last_print = -1

            while time.monotonic() < deadline:
                remaining = int(deadline - time.monotonic())
                if remaining != last_print:
                    last_print = remaining
                    print(f"    {remaining}s...")

                if radio.available():
                    raw = radio.read()
                    packets.append(bytes(raw))

            print(f"  Captured {len(packets)} packets")

            if len(packets) > 10:
                find_common_bytes(packets)
                find_common_ngrams(packets, 3)

                print(f"\n  First 5 packets:")
                for pkt in packets[:5]:
                    print(f"    {''.join(f'{b:02X}' for b in pkt[:20])}")

    try:
        radio.power_down()
    except Exception:
        pass

    print("\nDone.")


if __name__ == "__main__":
    main()
