#!/usr/bin/env python3
"""
C1 current test — effect of C1[0] value on white LED brightness.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, 4 pixels.

Only the white LED is on (D1-D4 = (W, 0, 0, 0)).
For each of 11 white levels, each of 6 C1 current values is tested
in the first C1 position, with positions 2-4 set to 0.

Example: C1=[0,0,0,0], C1=[1,0,0,0], C1=[16,0,0,0] ... C1=[63,0,0,0]

C1 byte format: bits[7:6]=00, bits[5:0]=current (0=6.5mA, 63=38mA)
C2 = bitwise NOT of C1

11 white values × 6 C1 values = 66 tests

Press Enter to advance. SPI stays open for seamless transitions.

Usage:
    python3 led_c1_current_test.py
"""

import sys
import tty
import termios
import threading
from spidev import SpiDev

NUM_LEDS = 6

WHITE_VALUES = [1, 26, 51, 77, 102, 128, 153, 179, 204, 230, 255]
C1_VALUES = [0, 1, 16, 32, 48, 63]


def encode_byte_4bit(value):
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001
        else:
            encoded = (encoded << 4) | 0b0111
    return bytes([
        (encoded >> 24) & 0xFF, (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF, encoded & 0xFF,
    ])


LUT_4BIT = [encode_byte_4bit(v) for v in range(256)]


def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes=80):
    """Build a TM1815B frame: [Reset][C1][C2][D1..Dn][Reset]"""
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf


def fmt_c(c_bytes):
    return f"[0x{c_bytes[0]:02X}, 0x{c_bytes[1]:02X}, 0x{c_bytes[2]:02X}, 0x{c_bytes[3]:02X}]"


def build_tests():
    tests = []
    for w_val in WHITE_VALUES:
        pixel = (w_val, 0, 0, 0)
        for c1_val in C1_VALUES:
            c1 = [c1_val, 0, 0, 0]
            c2 = [b ^ 0xFF for b in c1]
            current_ma = 6.5 + c1_val * 0.5
            tests.append({
                "w_val": w_val,
                "c1_val": c1_val,
                "current_ma": current_ma,
                "c1": c1,
                "c2": c2,
                "pixels": [pixel] * NUM_LEDS,
            })
    return tests


def main():
    tests = build_tests()
    total = len(tests)

    print("=" * 72)
    print("  C1 Current Test — White LED only")
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4 → PCB5 → PCB6")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (Section 5 baseline)")
    print(f"  D1-D4: (W, 0, 0, 0) — only white channel active")
    print(f"  C1: first position varied, other three = 0")
    print()
    print(f"  White values:  {WHITE_VALUES}")
    print(f"  C1[0] values:  {C1_VALUES}")
    print(f"  Total tests:   {total}")
    print()
    print("  Press Enter to advance. Ctrl+C to quit.")
    print("=" * 72)

    for t in tests:
        for j in range(4):
            if t["c2"][j] != (t["c1"][j] ^ 0xFF):
                print(f"  *** C2 validation error ***")
                sys.exit(1)

    bufs = []
    for t in tests:
        buf = build_frame(t["c1"], t["c2"], t["pixels"], LUT_4BIT, reset_bytes=80)
        bufs.append(list(buf))

    input("\n  Press Enter to start test 1...")

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = 2_000_000
    actual = spi.max_speed_hz
    spi.mode = 0b00
    spi.lsbfirst = False

    print(f"  SPI: requested 2.0 MHz, actual {actual/1e6:.3f} MHz\n")

    state = {'buf': bufs[0], 'running': True}
    lock = threading.Lock()

    def spi_loop():
        while state['running']:
            with lock:
                buf = state['buf']
            spi.xfer2(buf[:])

    spi_thread = threading.Thread(target=spi_loop, daemon=True)
    spi_thread.start()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    current_section_w = None

    try:
        tty.setraw(fd)
        for i, t in enumerate(tests):
            with lock:
                state['buf'] = bufs[i]

            if t["w_val"] != current_section_w:
                current_section_w = t["w_val"]
                sys.stdout.write(
                    f"\r\n  === D1-D4: W={current_section_w} R=0 G=0 B=0 "
                    f"(6 tests) ===\r\n")

            sys.stdout.write(
                f"\r\n  [{i+1:>2}/{total}] "
                f"W={t['w_val']:>3}  "
                f"C1[0]={t['c1_val']:>2} "
                f"({t['current_ma']:.1f}mA)  "
                f"C1={fmt_c(t['c1'])}\r\n")
            if i < total - 1:
                sys.stdout.write("         Enter -> next\r\n")
            else:
                sys.stdout.write("         Enter -> finish\r\n")
            sys.stdout.flush()

            while True:
                ch = sys.stdin.read(1)
                if ch == '\x03':
                    raise KeyboardInterrupt
                if ch in ('\r', '\n'):
                    break

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        state['running'] = False
        spi_thread.join(timeout=1)
        spi.close()
        print(f"\n  Done — {total} tests completed.")
        print("  Questions:")
        print("    1. Did changing C1[0] affect white LED brightness?")
        print("    2. Was the effect linear across C1 values 0->63?")
        print("    3. At which C1 value is the brightness change most visible?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
