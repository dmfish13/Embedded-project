#!/usr/bin/env python3
"""
RF button-press detector with LED visual feedback.

Listens for command packets (4C6D1765...) from the Jasco remote on the
nRF24L01+ (SPI0) and changes the LED strip color (SPI1) each time a
press is detected. LEDs start OFF, then cycle White -> Red -> Blue ->
Green on successive presses.

LED driver from led_marquee_cycle_test.py (4-bit encoding @ 2.0 MHz).

Usage:
    python3 rf_scanner_reset.py
    python3 rf_led_detect_test.py
"""

import time
from spidev import SpiDev

# -- nRF24L01+ imports (SPI0 via Blinka) --
import board
import busio
import digitalio


# ═══════════════════════════════════════════════════════════════════════
#  LED strip driver (SPI1) — from led_marquee_cycle_test.py
# ═══════════════════════════════════════════════════════════════════════

NUM_LEDS = 6
C1 = [0x20, 0x20, 0x20, 0x20]
C2 = [0xDF, 0xDF, 0xDF, 0xDF]
LED_SPI_SPEED = 2_000_000
RESET = 80


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


def build_frame(pixels):
    buf = bytearray(b'\xFF' * RESET)
    for bv in C1:
        buf += LUT_4BIT[bv]
    for bv in C2:
        buf += LUT_4BIT[bv]
    for w, r, g, b in pixels:
        buf += LUT_4BIT[w] + LUT_4BIT[r] + LUT_4BIT[g] + LUT_4BIT[b]
    buf += b'\xFF' * RESET
    return buf


OFF = (0, 0, 0, 0)

# Cycle: White -> Red -> Blue -> Green -> repeat
PRESS_COLORS = [
    (255, 0, 0, 0),      # white (W channel)
    (0, 255, 0, 0),      # red
    (0, 0, 0, 255),      # blue
    (0, 0, 255, 0),      # green
]


class LEDFlasher:
    def __init__(self):
        self.spi = SpiDev()
        self.spi.open(1, 0)
        self.spi.max_speed_hz = LED_SPI_SPEED
        self.spi.mode = 0b00
        self.spi.lsbfirst = False
        self._off_frame = list(build_frame([OFF] * NUM_LEDS))
        for _ in range(3):
            self.spi.xfer2(self._off_frame[:])

    def solid(self, wrgb):
        frame = list(build_frame([wrgb] * NUM_LEDS))
        for _ in range(3):
            self.spi.xfer2(frame[:])

    def off(self):
        for _ in range(3):
            self.spi.xfer2(self._off_frame[:])

    def close(self):
        self.off()
        self.spi.close()


# ═══════════════════════════════════════════════════════════════════════
#  nRF24L01+ RF receiver (SPI0)
# ═══════════════════════════════════════════════════════════════════════

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

    def configure_rx(self):
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


def is_command(desc):
    return desc[:4] == COMMAND_HEADER


def hamming_distance_bytes(a, b):
    dist = 0
    for x, y in zip(a, b):
        dist += bin(x ^ y).count('1')
    return dist


def is_near_command(desc, max_bit_errors=4):
    expected = bytes.fromhex("4C6D176547EC002D48FBDFD11649")
    return hamming_distance_bytes(desc[:14], expected) <= max_bit_errors


# ═══════════════════════════════════════════════════════════════════════
#  Main — RF detect + LED flash
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  RF Button-Press Detector with LED Feedback")
    print("  nRF24L01+ on SPI0 (ch 42) | LEDs on SPI1 (TM1815B)")
    print("=" * 70)
    print()
    print("  LEDs start OFF. Each button press on the remote cycles:")
    print("    OFF -> White -> Red -> Blue -> Green -> White -> ...")
    print()
    print("  Test procedure:")
    print("    1. Press NOTHING for 10s — LEDs stay off, no commands")
    print("    2. Press any button once — LEDs turn White")
    print("    3. Press again           — LEDs turn Red")
    print("    4. Press again           — LEDs turn Blue")
    print("    5. Press again           — LEDs turn Green")
    print("    6. Press again           — back to White")
    print()
    print("  Each button PRESS (not hold) should produce 1-3 command")
    print("  packets. Idle/interference packets are filtered out.")
    print()
    print("  Ctrl+C to quit.")
    print("=" * 70)

    # Init LED strip
    leds = LEDFlasher()
    print("  LED strip: OK (SPI1)")

    # Init RF radio
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure_rx()
    print("  nRF24L01+: OK (SPI0, ch 42)")
    print()

    COLOR_NAMES = ["White", "Red", "Blue", "Green"]

    press_count = 0
    cmd_count = 0
    idle_count = 0
    near_cmd_count = 0
    last_cmd_time = 0
    debounce = 0.3

    try:
        print("  Listening... (press remote buttons)")
        print(f"  {'Time':>8}  {'Event':<12}  {'Cmds':>5}  {'Presses':>7}  "
              f"{'Idle':>6}  {'Color':<8}  Payload")
        print("  " + "-" * 74)

        while True:
            if radio.available():
                raw = radio.read()
                desc = descramble(raw)

                if is_command(desc):
                    cmd_count += 1
                    now = time.monotonic()
                    hex_str = "".join(f"{b:02X}" for b in desc[:20])

                    if now - last_cmd_time > debounce:
                        press_count += 1
                        color_idx = (press_count - 1) % len(PRESS_COLORS)
                        color = PRESS_COLORS[color_idx]
                        color_name = COLOR_NAMES[color_idx]
                        leds.solid(color)

                    last_cmd_time = now
                    ts = time.strftime("%H:%M:%S")
                    print(f"  {ts}  {'COMMAND':<12}  {cmd_count:>5}  "
                          f"{press_count:>7}  {idle_count:>6}  "
                          f"{color_name:<8}  {hex_str}")

                elif is_near_command(desc):
                    near_cmd_count += 1

                else:
                    idle_count += 1
                    if idle_count % 100 == 0:
                        ts = time.strftime("%H:%M:%S")
                        print(f"  {ts}  {'idle':<12}  {cmd_count:>5}  "
                              f"{press_count:>7}  {idle_count:>6}  "
                              f"{'':8}  (noise/idle)")

    except KeyboardInterrupt:
        print()
        print()
        print("=" * 70)
        print("  RESULTS")
        print("=" * 70)
        print(f"  Command packets:      {cmd_count}")
        print(f"  Near-command packets:  {near_cmd_count}")
        print(f"  Idle/noise packets:    {idle_count}")
        print(f"  Button presses:        {press_count}")
        print(f"  (debounce: {debounce}s between presses)")
        print()
        if cmd_count > 0 and idle_count < cmd_count * 2:
            print("  GOOD: Most received packets are commands, not noise.")
        elif cmd_count > 0:
            print("  OK: Commands detected but lots of idle/noise too.")
            print("  (This is normal — the idle signal is always present)")
        else:
            print("  NO commands detected. Check:")
            print("    - Remote batteries")
            print("    - nRF24L01+ wiring")
            print("    - Run rf_scanner_reset.py first")
    finally:
        try:
            radio.power_down()
        except Exception:
            pass
        leds.close()
        print("  Radio + LEDs powered down.")


if __name__ == "__main__":
    main()
