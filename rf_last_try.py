#!/usr/bin/env python3
"""
Exhaustive Sync Word Search across all plausible nRF24-compatible
protocol configurations.

Tests 12 combinations of address patterns (including BK2421/BK2423
clone chip addresses), address widths (2-5 bytes), and all three
data rates (1 Mbps, 2 Mbps, 250 kbps) on channels 21, 42, and 64.

Flags configurations where 2+ byte positions show over 40% consistency
as possible matches. If no matches are found, the remote uses a
non-nRF24-compatible protocol.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_last_try.py
    (Hold POWER button during entire test)
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

    def configure(self, channel, addr, addr_width, data_rate=0x07):
        """Configure radio with specific address, width, and data rate.

        Args:
            channel: RF channel (0-125)
            addr: Address bytes (up to 5 bytes)
            addr_width: Address width in bytes (2-5)
            data_rate: RF_SETUP register value
                       0x07 = 1 Mbps, 0x0F = 2 Mbps, 0x27 = 250 kbps
        """
        self._ce.value = False
        self._write_reg(0x00, 0x03)
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)
        self._write_reg(0x02, 0x01)
        self._write_reg(0x03, addr_width - 2)
        self._write_reg_bytes(0x0A, addr[:addr_width])
        self._write_reg(0x11, 32)
        self._write_reg(0x06, data_rate)
        self._write_reg(0x1C, 0x00)
        self._write_reg(0x1D, 0x00)
        self._command(0xE2)
        self._command(0xE1)
        self._write_reg(0x07, 0x70)
        self._write_reg(0x05, channel)
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


# 12 protocol configurations to test
CONFIGS = [
    # Common BK2421/BK2423 (nRF24 clone) addresses
    {"addr": b"\xAA\x55\xAA\x55", "width": 4, "rate": 0x07, "label": "AA55AA55 4byte 1M"},
    {"addr": b"\x55\xAA\x55\xAA", "width": 4, "rate": 0x07, "label": "55AA55AA 4byte 1M"},
    {"addr": b"\x12\x34\x56", "width": 3, "rate": 0x07, "label": "123456 3byte 1M"},
    {"addr": b"\x71\x0C\x00", "width": 3, "rate": 0x07, "label": "710C00 3byte 1M"},
    {"addr": b"\xE7\xE7\xE7\xE7\xE7", "width": 5, "rate": 0x07, "label": "E7x5 default 1M"},
    {"addr": b"\xC2\xC2\xC2\xC2\xC2", "width": 5, "rate": 0x07, "label": "C2x5 default 1M"},
    # Same at 2 Mbps
    {"addr": b"\xAA\x55\xAA\x55", "width": 4, "rate": 0x0F, "label": "AA55AA55 4byte 2M"},
    {"addr": b"\x55\xAA\x55\xAA", "width": 4, "rate": 0x0F, "label": "55AA55AA 4byte 2M"},
    {"addr": b"\xE7\xE7\xE7\xE7\xE7", "width": 5, "rate": 0x0F, "label": "E7x5 default 2M"},
    {"addr": b"\xC2\xC2\xC2\xC2\xC2", "width": 5, "rate": 0x0F, "label": "C2x5 default 2M"},
    # 250 kbps
    {"addr": b"\xAA\x55\xAA\x55", "width": 4, "rate": 0x27, "label": "AA55AA55 4byte 250k"},
    {"addr": b"\xE7\xE7\xE7\xE7\xE7", "width": 5, "rate": 0x27, "label": "E7x5 default 250k"},
]


def scan_channel(radio, seconds):
    """Capture packets for the given duration.

    Returns:
        List of raw packet byte strings.
    """
    packets = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            packets.append(bytes(raw))
    return packets


def check_consistency(packets):
    """Check if any byte positions show consistent values.

    Returns:
        Tuple of (is_match: bool, num_consistent_positions: int).
        A match requires 2+ positions with >40% same-value occurrence.
    """
    if len(packets) < 5:
        return False, 0

    consistent = 0
    for pos in range(min(8, len(packets[0]))):
        values = [p[pos] for p in packets]
        counter = Counter(values)
        top_val, top_count = counter.most_common(1)[0]
        if top_count / len(packets) > 0.4:
            consistent += 1
    return consistent >= 2, consistent


def main():
    print("=" * 60)
    print("  Exhaustive Sync Word Search")
    print("  Hold POWER button during entire test")
    print("=" * 60)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    print("\n  HOLD the POWER button on the remote continuously.")
    print("  Do not release until the test says 'Done'.")
    input("  Press Enter to begin...\n")

    hits = []

    for channel in [21, 64, 42]:
        for cfg in CONFIGS:
            label = f"ch{channel} {cfg['label']}"
            radio.configure(channel, cfg["addr"], cfg["width"], cfg["rate"])

            packets = scan_channel(radio, 3)
            count = len(packets)

            if count > 0:
                is_consistent, num_consistent = check_consistency(packets)
                marker = " <<< POSSIBLE MATCH" if is_consistent else ""
                print(f"  {label:<40} {count:>4} pkts, "
                      f"{num_consistent} consistent bytes{marker}")

                if is_consistent:
                    hits.append({
                        "label": label,
                        "config": cfg,
                        "channel": channel,
                        "packets": packets,
                        "consistent": num_consistent,
                    })

                    print(f"    Sample: {''.join(f'{b:02X}' for b in packets[0][:16])}")
            else:
                print(f"  {label:<40}    0 pkts")

    if hits:
        print(f"\n{'='*60}")
        print("  MATCHES FOUND!")
        print(f"{'='*60}")
        for hit in hits:
            print(f"\n  {hit['label']}")
            print(f"  Config: addr={hit['config']['addr'].hex()}, "
                  f"width={hit['config']['width']}, "
                  f"rate={'1M' if hit['config']['rate']==0x07 else '2M' if hit['config']['rate']==0x0F else '250k'}")
            for pkt in hit["packets"][:5]:
                print(f"    {''.join(f'{b:02X}' for b in pkt[:20])}")
    else:
        print(f"\n  No consistent matches found.")
        print(f"  The remote likely uses a non-nRF24 compatible protocol.")
        print(f"  Consider using a CC2500 or BLE sniffer module instead.")

    try:
        radio.power_down()
    except Exception:
        pass

    print("\nDone.")


if __name__ == "__main__":
    main()
