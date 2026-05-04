#!/usr/bin/env python3
"""
RF channel scanner — scans all 126 nRF24L01+ channels.

Two passes: idle (no buttons) and active (hold a button).
Compares packet counts to find which channels the remote uses.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_channel_scan.py
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

    def configure(self):
        self._ce.value = False
        self._reg_write(0x00, 0x03)
        time.sleep(0.002)
        self._reg_write(0x01, 0x00)
        self._reg_write(0x02, 0x03)
        self._reg_write(0x03, 0x00)    # 2-byte address
        self._reg_write_bytes(0x0A, b"\x00\x55")
        self._reg_write_bytes(0x0B, b"\x00\xAA")
        self._reg_write(0x11, 32)
        self._reg_write(0x12, 32)
        self._reg_write(0x06, 0x07)    # 1 Mbps, 0 dBm
        self._reg_write(0x1C, 0x00)
        self._reg_write(0x1D, 0x00)
        self._cmd(0xE2)
        self._cmd(0xE1)
        self._reg_write(0x07, 0x70)

    def set_channel(self, ch):
        self._ce.value = False
        self._reg_write(0x05, ch)
        self._cmd(0xE2)                # flush RX between channels
        self._reg_write(0x07, 0x70)
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


def scan_all_channels(radio, dwell_ms=200, label=""):
    counts = [0] * 126
    samples = {}
    total_channels = 126

    for ch in range(total_channels):
        radio.set_channel(ch)
        deadline = time.monotonic() + (dwell_ms / 1000.0)
        while time.monotonic() < deadline:
            if radio.available():
                raw = radio.read()
                counts[ch] += 1
                if ch not in samples and counts[ch] <= 3:
                    samples.setdefault(ch, []).append(raw)

        if (ch + 1) % 20 == 0:
            print(f"    {label} ...ch {ch+1}/126")

    return counts, samples


def main():
    print("=" * 70)
    print("  RF Channel Scanner (all 126 channels)")
    print("  Promiscuous mode | 2-byte addr | no CRC | 1 Mbps")
    print("  Dwell: 200ms per channel per pass")
    print("=" * 70)
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24Promisc(spi, csn, ce)
    radio.configure()

    logfile = "audit_step2_channel_scan.txt"

    # Pass 1: idle
    print("  PASS 1: IDLE (do NOT press any buttons)")
    input("  Press Enter to start idle scan...")
    print("    Scanning 126 channels (takes ~25 seconds)...")
    idle_counts, idle_samples = scan_all_channels(radio, dwell_ms=200,
                                                   label="idle")
    idle_total = sum(idle_counts)
    print(f"    Idle scan done. Total packets: {idle_total}")
    print()

    # Pass 2: active
    print("  PASS 2: ACTIVE (HOLD the Power button the ENTIRE time)")
    input("  Hold Power button, then press Enter...")
    print("    Scanning 126 channels (takes ~25 seconds)...")
    active_counts, active_samples = scan_all_channels(radio, dwell_ms=200,
                                                       label="active")
    active_total = sum(active_counts)
    print(f"    Active scan done. Total packets: {active_total}")
    print()

    radio.power_down()

    # Analysis
    print("  Shutting down radio...")
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)

    with open(logfile, 'w') as log:
        log.write("RF Channel Scan Results\n")
        log.write(f"Date: {time.ctime()}\n")
        log.write(f"Dwell: 200ms per channel\n")
        log.write("=" * 70 + "\n\n")

        # Find channels with significant difference
        diffs = []
        for ch in range(126):
            diff = active_counts[ch] - idle_counts[ch]
            diffs.append((ch, idle_counts[ch], active_counts[ch], diff))

        # Print all channels with any activity
        header = f"  {'Ch':>4}  {'Freq':>8}  {'Idle':>6}  {'Active':>6}  {'Diff':>6}  Note"
        print(header)
        log.write(header + "\n")
        divider = "  " + "-" * 60
        print(divider)
        log.write(divider + "\n")

        hot_channels = []
        for ch, idle, active, diff in diffs:
            if idle > 0 or active > 0:
                freq = f"{2400 + ch} MHz"
                note = ""
                if diff > 5:
                    note = "<< CANDIDATE"
                    hot_channels.append((ch, diff))
                elif diff < -5:
                    note = "(less during press)"
                line = (f"  {ch:>4}  {freq:>8}  {idle:>6}  {active:>6}  "
                        f"{diff:>+6}  {note}")
                print(line)
                log.write(line + "\n")

        print()
        log.write("\n")

        # Summary
        if hot_channels:
            hot_channels.sort(key=lambda x: -x[1])
            print("  CANDIDATE CHANNELS (more packets during button press):")
            log.write("CANDIDATE CHANNELS:\n")
            for ch, diff in hot_channels:
                line = f"    Channel {ch} ({2400+ch} MHz): +{diff} packets"
                print(line)
                log.write(line + "\n")

            # Show sample packets from top channel
            top_ch = hot_channels[0][0]
            if top_ch in active_samples:
                print(f"\n  Sample packets from ch {top_ch}:")
                log.write(f"\nSample packets from ch {top_ch}:\n")
                for pkt in active_samples[top_ch][:3]:
                    hex_str = "".join(f"{b:02X}" for b in pkt)
                    print(f"    {hex_str}")
                    log.write(f"  {hex_str}\n")
        else:
            msg = "  NO channels showed significant increase during button press."
            print(msg)
            log.write(msg + "\n")

        print()
        print(f"  Results saved to {logfile}")
        print()
        print("  Done.")
        print("=" * 70)


if __name__ == "__main__":
    main()
