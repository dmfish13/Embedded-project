#!/usr/bin/env python3
"""
Rapid-tap button capture for BK2423/XN297 protocol analysis.

The remote only sends the real command packet (4C6D1765...) at the START
of each button press, then switches to idle carrier. This script asks you
to TAP the button repeatedly (not hold) to generate many command packets.

Filters out:
  - Idle carrier packets (4CEDAD0B...)
  - Bit-error variants of idle
  - Interference patterns

Keeps only clean 4C6D1765 command packets for analysis.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_tap_capture.py
"""

import time
import board
import busio
import digitalio
from collections import Counter


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

BUTTONS = [
    "Power", "Fade", "Dimming", "Strobe",
    "Color1", "2-hour", "Color2", "4-hour",
    "Modes", "Deep_Red", "Mint", "Dark_Blue",
    "Red_Prime", "Orange", "Light_Blue", "Violet",
    "Green_Prime", "Golden_Rod", "Cyan", "Purple",
    "Blue_Prime", "Yellow", "Steel_Blue", "Magenta",
    "White_Select",
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


def is_command_packet(desc):
    """Check if descrambled packet starts with the command header 4C6D1765."""
    return desc[:4] == COMMAND_HEADER


def hamming_distance(a, b):
    """Count differing bits between two byte sequences."""
    dist = 0
    for x, y in zip(a, b):
        dist += bin(x ^ y).count('1')
    return dist


def is_near_header(desc, max_bit_errors=3):
    """Check if first 14 bytes are close to expected header (allowing bit errors)."""
    expected = bytes.fromhex("4C6D176547EC002D48FBDFD11649")
    return hamming_distance(desc[:14], expected) <= max_bit_errors


def main():
    print("=" * 72)
    print("  Rapid-Tap Button Capture — Command Packet Extraction")
    print("  TAP each button rapidly (don't hold!) for 15 seconds")
    print("=" * 72)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure()

    logfile = "rf_tap_results.txt"
    all_results = {}
    capture_seconds = 15

    print(f"\n  Output: {logfile}")
    print(f"  Buttons: {len(BUTTONS)}")
    print(f"  TAP rapidly for {capture_seconds} seconds per button.")
    print(f"  Aim for 15-20 quick taps per session.\n")

    try:
        with open(logfile, 'w') as log:
            log.write("BK2423/XN297 Rapid-Tap Command Capture\n")
            log.write(f"Date: {time.ctime()}\n")
            log.write(f"Channel: {CHANNEL}, Rate: 1 Mbps\n")
            log.write(f"Header filter: 4C6D1765 (command packets only)\n")
            log.write("=" * 72 + "\n\n")

            for idx, button in enumerate(BUTTONS):
                print(f"  [{idx+1:2}/{len(BUTTONS)}] {button}")
                input(f"    Ready to tap [{button}] rapidly — press Enter to start...")
                print(f"    TAP NOW! Capturing for {capture_seconds}s...")

                commands = []
                near_commands = []
                total_rx = 0
                idle_count = 0
                deadline = time.monotonic() + capture_seconds

                while time.monotonic() < deadline:
                    if radio.available():
                        raw = radio.read()
                        desc = descramble(raw)
                        total_rx += 1

                        if is_command_packet(desc):
                            commands.append(desc)
                        elif is_near_header(desc):
                            near_commands.append(desc)
                        else:
                            idle_count += 1

                cmd_count = len(commands)
                print(f"    Total RX: {total_rx}, Commands: {cmd_count}, "
                      f"Near-cmd: {len(near_commands)}, Idle: {idle_count}")

                log.write(f"--- {button} ---\n")
                log.write(f"  Total packets: {total_rx}\n")
                log.write(f"  Command packets: {cmd_count}\n")
                log.write(f"  Near-command: {len(near_commands)}\n")
                log.write(f"  Idle/other: {idle_count}\n")

                if commands:
                    log.write(f"  Command payloads (descrambled, full 32 bytes):\n")
                    for c in commands:
                        hex_str = "".join(f"{b:02X}" for b in c)
                        log.write(f"    {hex_str}\n")

                    # Analyze byte 14 (first rolling byte)
                    byte14_vals = [c[14] for c in commands]
                    log.write(f"  Byte 14 values: {[f'{v:02X}' for v in byte14_vals]}\n")
                    log.write(f"  Byte 14 binary: {[f'{v:08b}' for v in byte14_vals]}\n")

                    # Check bytes 14-19 patterns
                    tails = [c[14:20] for c in commands]
                    log.write(f"  Tail bytes (14-19):\n")
                    for t in tails:
                        log.write(f"    {''.join(f'{b:02X}' for b in t)}\n")

                    # Bit-level consistency of byte 14
                    all_and = 0xFF
                    all_or = 0x00
                    for v in byte14_vals:
                        all_and &= v
                        all_or |= v
                    log.write(f"  Byte14 AND={all_and:08b} OR={all_or:08b}\n")
                    log.write(f"  Byte14 always-1: {all_and:08b}\n")
                    log.write(f"  Byte14 always-0: {(~all_or)&0xFF:08b}\n")

                    # Also check byte 15
                    byte15_vals = [c[15] for c in commands]
                    all_and15 = 0xFF
                    all_or15 = 0x00
                    for v in byte15_vals:
                        all_and15 &= v
                        all_or15 |= v
                    log.write(f"  Byte15 AND={all_and15:08b} OR={all_or15:08b}\n")

                    # Show on console
                    print(f"    Byte14 bits always-1: {all_and:08b}")
                    print(f"    Byte14 bits always-0: {(~all_or)&0xFF:08b}")

                    all_results[button] = {
                        "count": cmd_count,
                        "byte14_and": all_and,
                        "byte14_or": all_or,
                        "tails": tails,
                        "commands": commands,
                    }
                else:
                    print(f"    *** NO COMMAND PACKETS ***")
                    log.write(f"  NO COMMAND PACKETS\n")
                    all_results[button] = None

                log.write("\n")
                log.flush()
                print()

            # Summary analysis
            log.write("\n" + "=" * 72 + "\n")
            log.write("CROSS-BUTTON ANALYSIS\n")
            log.write("=" * 72 + "\n\n")

            print("\n" + "=" * 72)
            print("  CROSS-BUTTON ANALYSIS")
            print("=" * 72)

            buttons_with_data = {b: r for b, r in all_results.items()
                                 if r is not None and r["count"] >= 3}

            if buttons_with_data:
                print(f"\n  Buttons with 3+ command packets: {len(buttons_with_data)}")
                print()

                # For each button, show the consistent bits in byte 14
                print("  Byte 14 bit masks per button:")
                print(f"  {'Button':<16} {'Count':>5} {'AND':>10} {'OR':>10} {'Fixed':>10}")
                for button in BUTTONS:
                    r = buttons_with_data.get(button)
                    if r:
                        fixed = ~(r['byte14_and'] ^ r['byte14_or']) & 0xFF
                        line = (f"  {button:<16} {r['count']:>5} "
                                f"{r['byte14_and']:>08b}  {r['byte14_or']:>08b}  "
                                f"{fixed:>08b}")
                        print(line)
                        log.write(line + "\n")

                # Check if any bit positions in byte 14 differ between buttons
                # but are consistent WITHIN a button
                print("\n  Looking for button-discriminating bits...")
                log.write("\n  Button-discriminating bit analysis:\n")

                for bit in range(8):
                    per_button_values = {}
                    for button, r in buttons_with_data.items():
                        bit_vals = [(c[14] >> bit) & 1 for c in r["commands"]]
                        consistency = max(Counter(bit_vals).values()) / len(bit_vals)
                        majority = Counter(bit_vals).most_common(1)[0][0]
                        per_button_values[button] = (majority, consistency)

                    # A good discriminating bit is consistent within each button
                    # but different across buttons
                    avg_consistency = sum(c for _, c in per_button_values.values()) / len(per_button_values)
                    unique_values = len(set(m for m, _ in per_button_values.values()))
                    line = (f"    Bit {bit}: avg_consistency={avg_consistency:.2f}, "
                            f"unique_majority_values={unique_values}")
                    print(line)
                    log.write(line + "\n")

        print(f"\n  Results saved to {logfile}")

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
