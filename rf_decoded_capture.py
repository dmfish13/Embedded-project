#!/usr/bin/env python3
"""
Focused button capture using discovered BK2423/XN297 parameters.

Configuration discovered by rf_bk2423_scanner.py:
  - XN297 address: [0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
  - nRF24 address: [0x38, 0x72, 0x2D, 0xA8, 0x5E]
  - Scramble table: B (DeviationTX variant)
  - Bit reversal: yes
  - 5-byte address width, 1 Mbps, channel 42

Captures all 25 Jasco remote buttons and identifies the unique
byte pattern for each. Saves results to rf_decoded_buttons.txt.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_decoded_capture.py
"""

import time
import sys
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

# nRF24 address = bit-reversed XOR of [0xFF]*5 with SCRAMBLE_B[:5]
NRF24_ADDR = [0x38, 0x72, 0x2D, 0xA8, 0x5E]
ADDR_WIDTH = 5
CHANNEL = 42

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
        self._write_reg(0x00, 0x03)  # PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)  # no auto-ack
        self._write_reg(0x02, 0x01)  # enable pipe 0
        self._write_reg(0x03, ADDR_WIDTH - 2)
        self._write_reg_bytes(0x0A, bytes(NRF24_ADDR))
        self._write_reg(0x11, 32)    # 32-byte payload
        self._write_reg(0x06, 0x07)  # 1 Mbps
        self._write_reg(0x1C, 0x00)
        self._write_reg(0x1D, 0x00)
        self._command(0xE2)  # flush RX
        self._command(0xE1)  # flush TX
        self._write_reg(0x07, 0x70)  # clear flags
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
    """Descramble a raw payload: bit-reverse then XOR with scramble table."""
    result = bytearray(len(raw))
    for i in range(len(raw)):
        b = BIT_REVERSE[raw[i]]
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            result[i] = b ^ SCRAMBLE_B[idx]
        else:
            result[i] = b
    return bytes(result)


def find_static_bytes(payloads):
    """Find which byte positions are consistent across payloads."""
    if len(payloads) < 2:
        return list(range(len(payloads[0]))) if payloads else []
    static = []
    for pos in range(min(len(p) for p in payloads)):
        values = [p[pos] for p in payloads]
        if len(set(values)) == 1:
            static.append(pos)
    return static


def capture_button(radio, seconds=5):
    """Capture descrambled payloads for one button press."""
    payloads = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            desc = descramble(raw)
            payloads.append(desc)
    return payloads


def main():
    print("=" * 72)
    print("  Decoded Button Capture — BK2423/XN297 Protocol")
    print("  Address: [FF FF FF FF FF] (scrambled: [38 72 2D A8 5E])")
    print("  Channel 42 @ 1 Mbps, Scramble Table B + Bit Reversal")
    print("=" * 72)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure()

    logfile = "rf_decoded_buttons.txt"
    all_results = {}

    print(f"\n  Output: {logfile}")
    print(f"  Buttons: {len(BUTTONS)}")
    print(f"  Hold each button for 5 seconds when prompted.\n")

    try:
        with open(logfile, 'w') as log:
            log.write("BK2423/XN297 Decoded Button Capture\n")
            log.write(f"Date: {time.ctime()}\n")
            log.write(f"Address: [FF FF FF FF FF]\n")
            log.write(f"Channel: {CHANNEL}, Rate: 1 Mbps\n")
            log.write(f"Scramble: Table B + Bit Reversal\n")
            log.write("=" * 72 + "\n\n")

            for idx, button in enumerate(BUTTONS):
                print(f"  [{idx+1:2}/{len(BUTTONS)}] {button}")
                input(f"    Hold [{button}] and press Enter...")
                print(f"    Capturing for 5 seconds...")

                payloads = capture_button(radio, seconds=5)
                print(f"    Captured {len(payloads)} packets")

                log.write(f"--- {button} ({len(payloads)} packets) ---\n")

                if payloads:
                    # Find static bytes
                    static_pos = find_static_bytes(payloads)
                    static_hex = "".join(
                        f"{payloads[0][i]:02X}" if i in static_pos else ".."
                        for i in range(min(20, len(payloads[0])))
                    )

                    print(f"    Static pattern: {static_hex}")
                    print(f"    Static positions: {len(static_pos)}/{min(20, len(payloads[0]))}")

                    log.write(f"  Static: {static_hex}\n")
                    log.write(f"  Static positions: {static_pos}\n")

                    # Show all unique descrambled payloads (first 20 bytes)
                    seen = {}
                    for p in payloads:
                        key = p[:20]
                        hex_str = "".join(f"{b:02X}" for b in key)
                        seen[hex_str] = seen.get(hex_str, 0) + 1

                    log.write(f"  Unique patterns (first 20 bytes):\n")
                    for hex_str, count in sorted(seen.items(),
                                                  key=lambda x: -x[1]):
                        log.write(f"    {hex_str}  ({count}x)\n")

                    # Show the most common payload
                    top_hex = max(seen, key=seen.get)
                    top_count = seen[top_hex]
                    print(f"    Most common: {top_hex} ({top_count}x)")

                    all_results[button] = {
                        "count": len(payloads),
                        "static": static_hex,
                        "top_hex": top_hex,
                        "top_count": top_count,
                        "static_positions": static_pos,
                    }
                else:
                    print(f"    *** NO PACKETS ***")
                    log.write(f"  NO PACKETS CAPTURED\n")
                    all_results[button] = None

                log.write("\n")
                log.flush()
                print()

            # Summary table
            log.write("\n" + "=" * 72 + "\n")
            log.write("SUMMARY\n")
            log.write("=" * 72 + "\n\n")

            print("\n" + "=" * 72)
            print("  SUMMARY")
            print("=" * 72)

            for button in BUTTONS:
                r = all_results.get(button)
                if r:
                    line = (f"  {button:<16} {r['count']:>3} pkts  "
                            f"static={r['static']}")
                    print(line)
                    log.write(line + "\n")
                else:
                    line = f"  {button:<16}   0 pkts  NO DATA"
                    print(line)
                    log.write(line + "\n")

            # Find which byte positions distinguish buttons
            buttons_with_data = {b: r for b, r in all_results.items()
                                 if r is not None}
            if len(buttons_with_data) >= 2:
                log.write("\n\nBUTTON IDENTIFICATION BYTES\n")
                log.write("-" * 72 + "\n")
                print(f"\n  Identifying bytes (positions that differ between buttons):")

                top_payloads = {}
                for button, r in buttons_with_data.items():
                    hex_str = r["top_hex"]
                    payload_bytes = bytes.fromhex(hex_str)
                    top_payloads[button] = payload_bytes

                if top_payloads:
                    all_bytes = list(top_payloads.values())
                    diff_positions = []
                    for pos in range(min(len(b) for b in all_bytes)):
                        values = set(b[pos] for b in all_bytes)
                        if len(values) > 1:
                            diff_positions.append(pos)

                    log.write(f"  Differing positions: {diff_positions}\n\n")
                    print(f"  Positions: {diff_positions}")

                    for button in BUTTONS:
                        if button in top_payloads:
                            p = top_payloads[button]
                            diff_hex = " ".join(f"{p[i]:02X}" for i in diff_positions
                                               if i < len(p))
                            line = f"  {button:<16} -> {diff_hex}"
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
