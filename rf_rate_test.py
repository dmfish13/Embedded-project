#!/usr/bin/env python3
"""
RF data rate tester — tests 250 kbps, 1 Mbps, and 2 Mbps on channel 42.

Uses the known address [0x38, 0x72, 0x2D, 0xA8, 0x5E] and descrambles
with Table B + bit reversal. Counts how many packets descramble to known
headers at each rate.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_rate_test.py
"""

import time
import board
import busio
import digitalio


BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])

SCRAMBLE_B = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
]

NRF24_ADDR = [0x38, 0x72, 0x2D, 0xA8, 0x5E]
ADDR_WIDTH = 5
CHANNEL = 42

COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])
IDLE_HEADER = bytes([0x4C, 0xED, 0xAD, 0x0B])

RATES = [
    ("1 Mbps",   0x07),
    ("2 Mbps",   0x0F),
    ("250 kbps", 0x27),
]


class NRF24L01:
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

    def configure(self, rf_setup=0x07):
        self._ce.value = False
        self._reg_write(0x00, 0x03)    # PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._reg_write(0x01, 0x00)    # no auto-ack
        self._reg_write(0x02, 0x01)    # enable pipe 0 only
        self._reg_write(0x03, ADDR_WIDTH - 2)
        self._reg_write_bytes(0x0A, bytes(NRF24_ADDR))
        self._reg_write(0x11, 32)
        self._reg_write(0x06, rf_setup)
        self._reg_write(0x1C, 0x00)
        self._reg_write(0x1D, 0x00)
        self._cmd(0xE2)
        self._cmd(0xE1)
        self._reg_write(0x07, 0x70)
        self._reg_write(0x05, CHANNEL)
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


def descramble(raw):
    result = bytearray(len(raw))
    for i in range(len(raw)):
        b = BIT_REVERSE[raw[i]]
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            result[i] = b ^ SCRAMBLE_B[idx]
        else:
            result[i] = b
    return bytes(result)


def test_rate(radio, rate_name, rf_setup, seconds=10):
    radio.configure(rf_setup=rf_setup)
    time.sleep(0.01)

    total = 0
    commands = 0
    idle = 0
    other = 0
    samples = []

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            total += 1
            desc = descramble(raw)
            if desc[:4] == COMMAND_HEADER:
                commands += 1
                if len(samples) < 5:
                    samples.append(("CMD", desc))
            elif desc[:4] == IDLE_HEADER:
                idle += 1
                if len(samples) < 2:
                    samples.append(("IDLE", desc))
            else:
                other += 1
                if len(samples) < 1:
                    samples.append(("OTHER", desc))

    return total, commands, idle, other, samples


def main():
    print("=" * 70)
    print("  RF Data Rate Tester")
    print("  Channel 42 | Address [38 72 2D A8 5E] | Scramble B + BitRev")
    print("  Tests: 1 Mbps, 2 Mbps, 250 kbps (10 seconds each)")
    print("=" * 70)
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    logfile = "audit_step3_data_rate.txt"

    print("  HOLD the Power button for the ENTIRE test (~30 seconds).")
    input("  Press Enter when ready (keep holding Power!)...")
    print()

    results = []

    for rate_name, rf_setup in RATES:
        print(f"  Testing {rate_name}... (10 seconds)")
        total, cmds, idle, other, samples = test_rate(radio, rate_name,
                                                       rf_setup, seconds=10)
        results.append((rate_name, rf_setup, total, cmds, idle, other,
                        samples))
        print(f"    Total={total}  Commands={cmds}  Idle={idle}  Other={other}")

    radio.power_down()

    print()
    print("  Shutting down radio...")
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)

    with open(logfile, 'w') as log:
        log.write("RF Data Rate Test Results\n")
        log.write(f"Date: {time.ctime()}\n")
        log.write(f"Channel: {CHANNEL}\n")
        log.write(f"Address: {' '.join(f'{b:02X}' for b in NRF24_ADDR)}\n")
        log.write("=" * 70 + "\n\n")

        header = (f"  {'Rate':<12} {'Total':>6}  {'Commands':>8}  "
                  f"{'Idle':>6}  {'Other':>6}  Verdict")
        print(header)
        log.write(header + "\n")
        print("  " + "-" * 60)
        log.write("  " + "-" * 60 + "\n")

        best_rate = None
        best_cmds = -1

        for rate_name, rf_setup, total, cmds, idle, other, samples in results:
            if cmds > 0 or idle > 0:
                verdict = "VALID DATA"
                if cmds > best_cmds:
                    best_cmds = cmds
                    best_rate = rate_name
            elif total > 0:
                verdict = "noise only"
            else:
                verdict = "nothing"

            line = (f"  {rate_name:<12} {total:>6}  {cmds:>8}  "
                    f"{idle:>6}  {other:>6}  {verdict}")
            print(line)
            log.write(line + "\n")

            if samples:
                for label, desc in samples:
                    hex_str = "".join(f"{b:02X}" for b in desc[:20])
                    sline = f"    {label}: {hex_str}"
                    print(sline)
                    log.write(sline + "\n")

        print()
        log.write("\n")

        if best_rate:
            conclusion = f"  CONCLUSION: {best_rate} produces valid data."
            print(conclusion)
            log.write(conclusion + "\n")
        else:
            conclusion = "  CONCLUSION: No rate produced valid data. Check wiring/address."
            print(conclusion)
            log.write(conclusion + "\n")

    print(f"\n  Results saved to {logfile}")
    print()
    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
