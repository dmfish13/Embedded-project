#!/usr/bin/env python3
"""
RF noise validation test — determines if packets at the known address
are real signal or descramble artifacts from noise.

Tests the known address [38 72 2D A8 5E] on three channels:
  - Channel 42  (the expected remote channel)
  - Channel 100 (control — no expected activity)
  - Channel 80  (control — no expected activity)

If channels 100 and 80 show similar packet counts and "headers" as
channel 42, the address matching is broken and the headers are artifacts.
If only channel 42 shows packets, the signal is real.

Run with EVERYTHING off: remote batteries out, LEDs unplugged,
WiFi/BT disabled, phone/computer in airplane mode.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_noise_validation.py
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

COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])
IDLE_HEADER = bytes([0x4C, 0xED, 0xAD, 0x0B])

TEST_CHANNELS = [42, 100, 80]
SECONDS_PER_CHANNEL = 15


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

    def configure(self, channel):
        self._ce.value = False
        self._reg_write(0x00, 0x03)
        time.sleep(0.002)
        self._reg_write(0x01, 0x00)
        self._reg_write(0x02, 0x01)
        self._reg_write(0x03, ADDR_WIDTH - 2)
        self._reg_write_bytes(0x0A, bytes(NRF24_ADDR))
        self._reg_write(0x11, 32)
        self._reg_write(0x06, 0x07)
        self._reg_write(0x1C, 0x00)
        self._reg_write(0x1D, 0x00)
        self._cmd(0xE2)
        self._cmd(0xE1)
        self._reg_write(0x07, 0x70)
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


def test_channel(radio, channel, seconds):
    radio.configure(channel)
    time.sleep(0.01)

    total = 0
    commands = 0
    idle = 0
    other = 0
    samples = []

    deadline = time.monotonic() + seconds
    last_heartbeat = time.monotonic()

    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            total += 1
            desc = descramble(raw)

            if desc[:4] == COMMAND_HEADER:
                commands += 1
                label = "CMD"
            elif desc[:4] == IDLE_HEADER:
                idle += 1
                label = "IDLE"
            else:
                other += 1
                label = "OTHER"

            if len(samples) < 5:
                samples.append((label, desc))

        now = time.monotonic()
        if now - last_heartbeat >= 3.0:
            elapsed = now - (deadline - seconds)
            print(f"    {elapsed:.0f}s: {total} pkts so far "
                  f"(cmd={commands} idle={idle} other={other})")
            last_heartbeat = now

    return total, commands, idle, other, samples


def main():
    print("=" * 70)
    print("  RF Noise Validation Test")
    print("  Address: [38 72 2D A8 5E] | 1 Mbps | Scramble B + BitRev")
    print(f"  Channels: {TEST_CHANNELS} | {SECONDS_PER_CHANNEL}s each")
    print("=" * 70)
    print()
    print("  IMPORTANT: Remove remote batteries, unplug LEDs,")
    print("  disable WiFi/BT, phone/computer in airplane mode.")
    print()
    input("  Press Enter when ready...")
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    logfile = "audit_noise_validation.txt"
    results = []

    for ch in TEST_CHANNELS:
        print(f"  Channel {ch} ({2400 + ch} MHz) — "
              f"listening {SECONDS_PER_CHANNEL}s...")
        total, cmds, idle_cnt, other, samples = test_channel(
            radio, ch, SECONDS_PER_CHANNEL)
        results.append((ch, total, cmds, idle_cnt, other, samples))
        print(f"    Result: {total} pkts "
              f"(cmd={cmds} idle={idle_cnt} other={other})")
        print()

    radio.power_down()
    print("  Shutting down radio...")
    print()

    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)
    print()

    header = (f"  {'Channel':>8}  {'Freq':>8}  {'Total':>6}  "
              f"{'Cmd':>5}  {'Idle':>5}  {'Other':>5}  {'Rate':>7}")
    print(header)
    print("  " + "-" * 58)

    with open(logfile, 'w') as log:
        log.write("RF Noise Validation Test\n")
        log.write(f"Date: {time.ctime()}\n")
        log.write(f"Address: {' '.join(f'{b:02X}' for b in NRF24_ADDR)}\n")
        log.write(f"Seconds per channel: {SECONDS_PER_CHANNEL}\n")
        log.write("=" * 70 + "\n\n")
        log.write(header + "\n")
        log.write("  " + "-" * 58 + "\n")

        for ch, total, cmds, idle_cnt, other, samples in results:
            rate = total / SECONDS_PER_CHANNEL
            line = (f"  {ch:>8}  {2400+ch:>5} MHz  {total:>6}  "
                    f"{cmds:>5}  {idle_cnt:>5}  {other:>5}  "
                    f"{rate:>5.1f}/s")
            print(line)
            log.write(line + "\n")

            for label, desc in samples:
                hex_str = "".join(f"{b:02X}" for b in desc)
                sline = f"    {label}: {hex_str}"
                print(sline)
                log.write(sline + "\n")

        print()
        log.write("\n")

        ch42 = next((r for r in results if r[0] == 42), None)
        others = [r for r in results if r[0] != 42]

        if ch42:
            ch42_total = ch42[1]
            other_totals = [r[1] for r in others]
            avg_other = sum(other_totals) / len(other_totals) if other_totals else 0

            if ch42_total <= avg_other * 1.5 and ch42_total > 0:
                verdict = ("NOISE: All channels show similar packet counts.\n"
                           "  The address matching is likely broken or the\n"
                           "  descrambled headers are artifacts of the\n"
                           "  scramble table applied to noise.")
            elif ch42_total > avg_other * 3 and ch42_total > 10:
                verdict = ("REAL SIGNAL: Channel 42 has significantly more\n"
                           "  packets than control channels. Something is\n"
                           "  transmitting on ch 42 at this address.")
            elif ch42_total == 0 and avg_other == 0:
                verdict = ("SILENT: No packets on any channel. The radio\n"
                           "  environment is clean — no transmitters active.")
            else:
                verdict = ("INCONCLUSIVE: Results are ambiguous. Check the\n"
                           "  raw numbers and sample packets above.")

            conclusion = f"  VERDICT: {verdict}"
            print(conclusion)
            log.write(conclusion + "\n")

    print()
    print(f"  Results saved to {logfile}")
    print()
    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
