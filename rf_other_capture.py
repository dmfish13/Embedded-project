#!/usr/bin/env python3
"""
Capture ALL packet types — focus on "OTHER" packets that don't match
the CMD (4C6D1765) or IDLE (4CEDAD0B) headers.

The remote's actual signal is likely in these OTHER packets, not the CMD
packets (which come from the unknown background source).

Runs 3 phases:
  1. Control — no button pressed (15s)
  2. Hold Power button (15s)
  3. Hold a color button (15s)

Records every OTHER packet's full hex for comparison.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_other_capture.py
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

    def configure(self):
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
        self._reg_write(0x05, 42)
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


def to_hex(data):
    return "".join(f"{b:02X}" for b in data)


def capture_phase(radio, label, seconds):
    print(f"\n  --- {label} ({seconds}s) ---")
    cmd_count = 0
    idle_count = 0
    other_packets = []
    total = 0

    deadline = time.monotonic() + seconds
    last_print = time.monotonic()

    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            desc = descramble(raw)
            total += 1

            if desc[:4] == COMMAND_HEADER:
                cmd_count += 1
            elif desc[:4] == IDLE_HEADER:
                idle_count += 1
            else:
                other_packets.append(desc)

        now = time.monotonic()
        if now - last_print >= 5.0:
            print(f"    {now - (deadline - seconds):.0f}s: total={total} "
                  f"cmd={cmd_count} idle={idle_count} other={len(other_packets)}")
            last_print = now

    print(f"    Done: total={total} cmd={cmd_count} "
          f"idle={idle_count} other={len(other_packets)}")
    return cmd_count, idle_count, other_packets


def main():
    print("=" * 70)
    print("  OTHER Packet Capture — Finding the Remote's Actual Signal")
    print("  Channel 42 | Address [38 72 2D A8 5E] | 1 Mbps")
    print("=" * 70)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure()

    logfile = "rf_other_capture_results.txt"
    phases = []

    try:
        # Phase 1: Control
        input("\n  Phase 1: CONTROL — no button, batteries OUT. Press Enter...")
        cmd1, idle1, other1 = capture_phase(radio, "Control", 15)
        phases.append(("Control (no button)", cmd1, idle1, other1))

        # Phase 2: Hold Power
        input("\n  Phase 2: HOLD POWER — press and hold Power. Press Enter...")
        cmd2, idle2, other2 = capture_phase(radio, "Hold Power", 15)
        phases.append(("Hold Power", cmd2, idle2, other2))

        # Phase 3: Hold Red
        input("\n  Phase 3: HOLD RED — press and hold Red. Press Enter...")
        cmd3, idle3, other3 = capture_phase(radio, "Hold Red", 15)
        phases.append(("Hold Red", cmd3, idle3, other3))

        # Phase 4: Hold Blue
        input("\n  Phase 4: HOLD BLUE — press and hold Blue. Press Enter...")
        cmd4, idle4, other4 = capture_phase(radio, "Hold Blue", 15)
        phases.append(("Hold Blue", cmd4, idle4, other4))

    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        radio.power_down()

    with open(logfile, 'w') as log:
        log.write("OTHER Packet Capture Results\n")
        log.write(f"Date: {time.ctime()}\n")
        log.write(f"Channel: 42 | Address: {' '.join(f'{b:02X}' for b in NRF24_ADDR)}\n")
        log.write("=" * 70 + "\n\n")

        for label, cmd_c, idle_c, others in phases:
            total = cmd_c + idle_c + len(others)
            log.write(f"--- {label} ---\n")
            log.write(f"  Total: {total}  CMD: {cmd_c}  IDLE: {idle_c}  "
                      f"OTHER: {len(others)}\n")

            if others:
                log.write(f"  OTHER packets (descrambled, full 32 bytes):\n")
                for pkt in others:
                    log.write(f"    {to_hex(pkt)}\n")

                # Group by first 4 bytes to find common headers
                prefix_groups = {}
                for pkt in others:
                    prefix = to_hex(pkt[:4])
                    if prefix not in prefix_groups:
                        prefix_groups[prefix] = []
                    prefix_groups[prefix].append(pkt)

                log.write(f"\n  OTHER prefix groups (first 4 bytes):\n")
                for prefix, pkts in sorted(prefix_groups.items(),
                                           key=lambda x: -len(x[1])):
                    log.write(f"    {prefix}: {len(pkts)} packets\n")
                    for p in pkts[:3]:
                        log.write(f"      {to_hex(p)}\n")
                    if len(pkts) > 3:
                        log.write(f"      ... ({len(pkts)-3} more)\n")

                # Group by first 8 bytes for finer matching
                prefix8_groups = {}
                for pkt in others:
                    prefix = to_hex(pkt[:8])
                    if prefix not in prefix8_groups:
                        prefix8_groups[prefix] = []
                    prefix8_groups[prefix].append(pkt)

                repeats = {k: v for k, v in prefix8_groups.items() if len(v) >= 2}
                if repeats:
                    log.write(f"\n  Repeated 8-byte prefixes (2+ matches):\n")
                    for prefix, pkts in sorted(repeats.items(),
                                               key=lambda x: -len(x[1])):
                        log.write(f"    {prefix}: {len(pkts)} times\n")
                        for p in pkts[:5]:
                            log.write(f"      {to_hex(p)}\n")

            log.write("\n")

        # Cross-phase comparison
        log.write("=" * 70 + "\n")
        log.write("CROSS-PHASE COMPARISON\n")
        log.write("=" * 70 + "\n\n")

        control_prefixes = set()
        if phases:
            for pkt in phases[0][3]:
                control_prefixes.add(to_hex(pkt[:4]))

        for label, _, _, others in phases[1:]:
            button_prefixes = set()
            for pkt in others:
                button_prefixes.add(to_hex(pkt[:4]))

            new_prefixes = button_prefixes - control_prefixes
            if new_prefixes:
                log.write(f"  {label} — NEW prefixes (not in control):\n")
                for prefix in sorted(new_prefixes):
                    matching = [p for p in others if to_hex(p[:4]) == prefix]
                    log.write(f"    {prefix}: {len(matching)} packets\n")
                    for p in matching[:5]:
                        log.write(f"      {to_hex(p)}\n")
                log.write("\n")
            else:
                log.write(f"  {label} — no new prefixes vs control\n\n")

    print(f"\n  Results saved to {logfile}")
    print("  Done.")


if __name__ == "__main__":
    main()
