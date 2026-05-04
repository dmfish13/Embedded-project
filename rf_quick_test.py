#!/usr/bin/env python3
"""
Quick test: capture two different buttons and compare FULL 32-byte payloads.

Tests whether the button ID is in bytes 20-31 (which were previously truncated).
Captures Power and Color1 for comparison.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_quick_test.py
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

    def configure(self):
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
        self._write_reg(0x05, CHANNEL)
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


def capture_commands(radio, label, seconds=20):
    """Capture command packets (4C6D1765...) during rapid tapping."""
    print(f"\n  --- {label} ---")
    input(f"  TAP [{label}] rapidly for {seconds}s. Press Enter to start...")
    print(f"  Capturing...")

    commands = []
    all_packets = []
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            desc = descramble(raw)
            all_packets.append(desc)
            if desc[:4] == COMMAND_HEADER:
                commands.append(desc)
                # Print immediately
                hex_full = "".join(f"{b:02X}" for b in desc)
                print(f"    CMD: {hex_full}")

    print(f"  Total: {len(all_packets)} packets, {len(commands)} commands")
    return commands, all_packets


def main():
    print("=" * 72)
    print("  Quick Button Comparison Test (Full 32-byte payloads)")
    print("  Tests whether button ID is in bytes 20-31")
    print("=" * 72)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure()

    try:
        # Capture two different buttons
        cmds_a, all_a = capture_commands(radio, "Power", seconds=20)
        cmds_b, all_b = capture_commands(radio, "Color1", seconds=20)

        print("\n" + "=" * 72)
        print("  COMPARISON")
        print("=" * 72)

        if cmds_a and cmds_b:
            print(f"\n  Power commands ({len(cmds_a)}):")
            for c in cmds_a:
                print(f"    {''.join(f'{b:02X}' for b in c)}")

            print(f"\n  Color1 commands ({len(cmds_b)}):")
            for c in cmds_b:
                print(f"    {''.join(f'{b:02X}' for b in c)}")

            # Compare byte-by-byte
            print("\n  Byte consistency within Power:")
            if len(cmds_a) >= 2:
                for pos in range(32):
                    vals = set(c[pos] for c in cmds_a)
                    if len(vals) == 1:
                        print(f"    Byte {pos:2d}: always {cmds_a[0][pos]:02X}")

            print("\n  Byte consistency within Color1:")
            if len(cmds_b) >= 2:
                for pos in range(32):
                    vals = set(c[pos] for c in cmds_b)
                    if len(vals) == 1:
                        print(f"    Byte {pos:2d}: always {cmds_b[0][pos]:02X}")

            # Check bytes 20-31 specifically
            print("\n  Bytes 20-31 comparison:")
            print("  Power:")
            for c in cmds_a[:5]:
                print(f"    {''.join(f'{b:02X}' for b in c[20:])}")
            print("  Color1:")
            for c in cmds_b[:5]:
                print(f"    {''.join(f'{b:02X}' for b in c[20:])}")
        else:
            if not cmds_a:
                print("  No Power commands captured - try tapping faster!")
            if not cmds_b:
                print("  No Color1 commands captured - try tapping faster!")

        # Also show non-command packets for analysis
        print(f"\n  Non-command packets (first 5 of each, full 32 bytes):")
        print("  Power non-cmd:")
        non_cmd_a = [p for p in all_a if p[:4] != COMMAND_HEADER][:5]
        for p in non_cmd_a:
            print(f"    {''.join(f'{b:02X}' for b in p)}")
        print("  Color1 non-cmd:")
        non_cmd_b = [p for p in all_b if p[:4] != COMMAND_HEADER][:5]
        for p in non_cmd_b:
            print(f"    {''.join(f'{b:02X}' for b in p)}")

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
    finally:
        try:
            radio.power_down()
        except Exception:
            pass
        print("\n  Radio powered down.")


if __name__ == "__main__":
    main()
