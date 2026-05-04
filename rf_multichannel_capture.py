#!/usr/bin/env python3
"""
Multi-channel capture — Check if button ID differs across channels 21, 42, 64.

The remote may send different data on different channels, or the button ID
might be encoded in the channel hopping pattern. This script captures
command packets on all three channels and compares them.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_multichannel_capture.py
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
CHANNELS = [21, 42, 64]

COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])


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

    def configure(self, channel):
        self._ce.value = False
        self._write_reg(0x00, 0x03)
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)
        self._write_reg(0x02, 0x01)
        self._write_reg(0x03, ADDR_WIDTH - 2)
        self._write_reg_bytes(0x0A, bytes(NRF24_ADDR))
        self._write_reg(0x11, 32)
        self._write_reg(0x06, 0x07)
        self._write_reg(0x1C, 0x00)
        self._write_reg(0x1D, 0x00)
        self._command(0xE2)
        self._command(0xE1)
        self._write_reg(0x07, 0x70)
        self._write_reg(0x05, channel)
        self._ce.value = True

    def set_channel(self, ch):
        self._ce.value = False
        self._command(0xE2)  # flush RX
        self._write_reg(0x07, 0x70)
        self._write_reg(0x05, ch)
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


def main():
    print("=" * 72)
    print("  Multi-Channel Command Capture")
    print("  Channels: 21, 42, 64 — Round-robin with 10ms dwell")
    print("=" * 72)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure(42)

    buttons_to_test = ["Power", "Color1", "Red_Prime"]

    try:
        for button in buttons_to_test:
            print(f"\n  --- {button} ---")
            print(f"  TAP [{button}] rapidly for 20 seconds.")
            input(f"  Press Enter to start...")
            print(f"  Capturing (round-robin ch 21/42/64)...")

            ch_commands = {ch: [] for ch in CHANNELS}
            ch_all = {ch: 0 for ch in CHANNELS}
            deadline = time.monotonic() + 20

            while time.monotonic() < deadline:
                for ch in CHANNELS:
                    radio.set_channel(ch)
                    ch_deadline = time.monotonic() + 0.010  # 10ms per channel
                    while time.monotonic() < ch_deadline:
                        if radio.available():
                            raw = radio.read()
                            desc = descramble(raw)
                            ch_all[ch] += 1
                            if desc[:4] == COMMAND_HEADER:
                                hex_desc = "".join(f"{b:02X}" for b in desc)
                                ch_commands[ch].append(hex_desc)
                                print(f"    ch{ch}: {hex_desc[:40]}...")

            print(f"\n  Results for {button}:")
            for ch in CHANNELS:
                print(f"    Channel {ch}: {ch_all[ch]} total, {len(ch_commands[ch])} commands")
                for cmd in ch_commands[ch][:3]:
                    print(f"      {cmd}")

            # Compare across channels
            if any(ch_commands[ch] for ch in CHANNELS):
                print(f"\n  Cross-channel comparison (first 14 bytes):")
                for ch in CHANNELS:
                    if ch_commands[ch]:
                        headers = set(cmd[:28] for cmd in ch_commands[ch])
                        print(f"    ch{ch}: {len(headers)} unique headers")
                        for h in list(headers)[:2]:
                            print(f"      {h}")

        print(f"\n  Done.")

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
    finally:
        try:
            radio.power_down()
        except Exception:
            pass
        print("  Radio powered down.")


if __name__ == "__main__":
    main()
