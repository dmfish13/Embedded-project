#!/usr/bin/env python3
"""
C1 current test — effect of C1 values on white LED brightness.

Uses the same format as led_c1c2_test.py Section 5 baseline:
  4-bit encoding @ 2.0 MHz, 4 pixels.

Only the white LED is on (D1-D4 = (W, 0, 0, 0)).
For each of 5 white levels, each of 6 C1 current values is tested
individually in each of the 4 C1 byte positions, with the other
3 positions set to 0.

Example for W=1:
  C1=[0,0,0,0]  C1=[1,0,0,0]  C1=[16,0,0,0] ... C1=[63,0,0,0]
  C1=[0,0,0,0]  C1=[0,1,0,0]  C1=[0,16,0,0] ... C1=[0,63,0,0]
  C1=[0,0,0,0]  C1=[0,0,1,0]  C1=[0,0,16,0] ... C1=[0,0,63,0]
  C1=[0,0,0,0]  C1=[0,0,0,1]  C1=[0,0,0,16] ... C1=[0,0,0,63]

C1 byte format: bits[7:6]=00, bits[5:0]=current (0=6.5mA, 63=38mA)
C2 = bitwise NOT of C1

5 white values × 4 positions × 6 C1 values = 120 tests

Press Enter to advance. SPI stays open for seamless transitions.

Usage:
    python3 led_c1_current_test.py
"""

import sys
import tty
import termios
import threading
from spidev import SpiDev

NUM_LEDS = 4

WHITE_VALUES = [1, 64, 128, 192, 255]
C1_VALUES = [0, 1, 16, 32, 48, 63]
POSITION_NAMES = ["W", "R", "G", "B"]


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
        for pos in range(4):
            for c1_val in C1_VALUES:
                c1 = [0, 0, 0, 0]
                c1[pos] = c1_val
                c2 = [b ^ 0xFF for b in c1]
                current_ma = 6.5 + c1_val * 0.5
                tests.append({
                    "w_val": w_val,
                    "c1_val": c1_val,
                    "pos": pos,
                    "pos_name": POSITION_NAMES[pos],
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
    print(f"  {NUM_LEDS} PCBs: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Format: 4-bit encoding @ 2.0 MHz (Section 5 baseline)")
    print(f"  D1-D4: (W, 0, 0, 0) — only white channel active")
    print(f"  C1: one position varied, other three = 0")
    print()
    print(f"  White values:  {WHITE_VALUES}")
    print(f"  C1 values:     {C1_VALUES}")
    print(f"  C1 positions:  {POSITION_NAMES}")
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
    current_section_pos = None

    try:
        tty.setraw(fd)
        for i, t in enumerate(tests):
            with lock:
                state['buf'] = bufs[i]

            if t["w_val"] != current_section_w:
                current_section_w = t["w_val"]
                sys.stdout.write(
                    f"\r\n  === D1-D4: W={current_section_w} R=0 G=0 B=0 "
                    f"===\r\n")
                current_section_pos = None

            if t["pos"] != current_section_pos:
                current_section_pos = t["pos"]
                sys.stdout.write(
                    f"\r\n    -- C1 position {t['pos_name']} "
                    f"(6 tests) --\r\n")

            sys.stdout.write(
                f"\r\n  [{i+1:>3}/{total}] "
                f"W={t['w_val']:>3}  "
                f"C1[{t['pos_name']}]={t['c1_val']:>2} "
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
        print("    1. Did changing C1[W] affect white LED brightness?")
        print("    2. Did changing C1[R], C1[G], or C1[B] have any effect?")
        print("    3. Was the effect linear across C1 values 0->63?")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
