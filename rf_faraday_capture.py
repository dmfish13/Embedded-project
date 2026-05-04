#!/usr/bin/env python3
"""
Faraday cage capture — optimized for reduced-noise environment.

Captures all packets and highlights short-payload packets (idle tail)
which are the remote's actual signal. With background noise reduced,
the remote's clean packets should dominate.

Phases:
  1. Baseline — no button, 10s (measure remaining background)
  2. Hold each button for 10s

Usage:
    python3 rf_scanner_reset.py
    python3 rf_faraday_capture.py
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

BUTTONS = [
    "Power", "Fade", "Dimming", "Strobe",
    "Color1", "Modes", "Color2",
    "Red", "Green", "Blue", "White",
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


def to_raw(desc_bytes):
    raw = []
    for i in range(len(desc_bytes)):
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            bit_rev = desc_bytes[i] ^ SCRAMBLE_B[idx]
        else:
            bit_rev = desc_bytes[i]
        raw.append(BIT_REVERSE[bit_rev])
    return bytes(raw)


def to_hex(data):
    return "".join(f"{b:02X}" for b in data)


def has_idle_tail(desc_bytes):
    raw = to_raw(desc_bytes)
    tail = raw[25:]
    if len(tail) < 5:
        return False
    for idle_val in (0xFF, 0x55, 0xAA):
        if all(b == idle_val or bin(b ^ idle_val).count('1') <= 1 for b in tail):
            return True
    return False


def find_idle_start(desc_bytes):
    raw = to_raw(desc_bytes)
    if len(raw) < 5:
        return len(raw)
    tail_val = raw[-1]
    if tail_val not in (0xFF, 0x55, 0xAA):
        return len(raw)
    start = len(raw)
    for i in range(len(raw) - 2, -1, -1):
        if raw[i] == tail_val or bin(raw[i] ^ tail_val).count('1') <= 1:
            start = i
        else:
            break
    return start


def capture_phase(radio, seconds):
    all_packets = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            desc = descramble(raw)
            all_packets.append(desc)
    return all_packets


def classify(desc):
    if desc[:4] == COMMAND_HEADER:
        return "CMD"
    elif desc[:4] == IDLE_HEADER:
        return "IDLE"
    elif has_idle_tail(desc):
        return "SHORT"
    else:
        return "OTHER"


def main():
    print("=" * 70)
    print("  Faraday Cage Capture")
    print("  Channel 42 | Address [38 72 2D A8 5E] | 1 Mbps")
    print("=" * 70)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)
    radio.configure()

    logfile = "rf_faraday_results.txt"
    results = []
    capture_seconds = 10

    try:
        # Baseline
        input("\n  BASELINE: No button, batteries OUT. Press Enter...")
        print(f"  Capturing {capture_seconds}s...")
        pkts = capture_phase(radio, capture_seconds)
        results.append(("Baseline", pkts))
        counts = {}
        for p in pkts:
            c = classify(p)
            counts[c] = counts.get(c, 0) + 1
        print(f"  Got {len(pkts)} packets: {counts}")
        short_count = sum(1 for p in pkts if classify(p) == "SHORT")
        if short_count > 0:
            print(f"  ** {short_count} SHORT (idle-tail) packets in baseline!")

        # Each button
        for btn in BUTTONS:
            input(f"\n  HOLD [{btn}] — press and hold, then press Enter...")
            print(f"  Capturing {capture_seconds}s (keep holding!)...")
            pkts = capture_phase(radio, capture_seconds)
            results.append((btn, pkts))
            counts = {}
            for p in pkts:
                c = classify(p)
                counts[c] = counts.get(c, 0) + 1
            print(f"  Got {len(pkts)} packets: {counts}")

            short_pkts = [p for p in pkts if classify(p) == "SHORT"]
            if short_pkts:
                print(f"  ** {len(short_pkts)} SHORT packets!")
                for sp in short_pkts[:3]:
                    idle_at = find_idle_start(sp)
                    print(f"     {to_hex(sp[:idle_at])}... (payload {idle_at} bytes)")

    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        radio.power_down()

    with open(logfile, 'w') as log:
        log.write("Faraday Cage Capture Results\n")
        log.write(f"Date: {time.ctime()}\n")
        log.write(f"Channel: 42 | Address: {' '.join(f'{b:02X}' for b in NRF24_ADDR)}\n")
        log.write(f"Capture: {capture_seconds}s per phase\n")
        log.write("=" * 70 + "\n\n")

        for label, pkts in results:
            counts = {}
            for p in pkts:
                c = classify(p)
                counts[c] = counts.get(c, 0) + 1

            rate = len(pkts) / capture_seconds
            log.write(f"--- {label} ---\n")
            log.write(f"  Total: {len(pkts)} ({rate:.1f}/s)\n")
            log.write(f"  CMD: {counts.get('CMD',0)}  IDLE: {counts.get('IDLE',0)}  "
                      f"SHORT: {counts.get('SHORT',0)}  OTHER: {counts.get('OTHER',0)}\n")

            # Show ALL SHORT packets (remote's signal)
            short_pkts = [p for p in pkts if classify(p) == "SHORT"]
            if short_pkts:
                log.write(f"\n  SHORT packets (remote signal, descrambled):\n")
                for sp in short_pkts:
                    idle_at = find_idle_start(sp)
                    raw = to_raw(sp)
                    log.write(f"    desc: {to_hex(sp)}\n")
                    log.write(f"    raw:  {to_hex(raw)}\n")
                    log.write(f"    payload_len: {idle_at}\n\n")

            # Show all unique packet prefixes (first 8 bytes) with counts
            prefix_counts = {}
            for p in pkts:
                pfx = to_hex(p[:8])
                prefix_counts[pfx] = prefix_counts.get(pfx, 0) + 1

            log.write(f"\n  Prefix distribution (first 8 bytes, descrambled):\n")
            for pfx, cnt in sorted(prefix_counts.items(), key=lambda x: -x[1])[:15]:
                log.write(f"    {pfx}: {cnt}\n")

            # Show ALL non-IDLE, non-CMD packets
            other_pkts = [p for p in pkts if classify(p) not in ("CMD", "IDLE")]
            if other_pkts:
                log.write(f"\n  All non-standard packets ({len(other_pkts)}):\n")
                for op in other_pkts:
                    c = classify(op)
                    raw = to_raw(op)
                    log.write(f"    [{c:5s}] desc: {to_hex(op)}\n")
                    log.write(f"            raw:  {to_hex(raw)}\n")

            log.write("\n")

        # Cross-phase: find SHORT packet patterns per button
        log.write("=" * 70 + "\n")
        log.write("SHORT PACKET COMPARISON ACROSS BUTTONS\n")
        log.write("=" * 70 + "\n\n")

        for label, pkts in results:
            short_pkts = [p for p in pkts if classify(p) == "SHORT"]
            if short_pkts:
                log.write(f"  {label}: {len(short_pkts)} short packets\n")
                for sp in short_pkts:
                    raw = to_raw(sp)
                    idle_at = find_idle_start(sp)
                    log.write(f"    raw[0:15]: {to_hex(raw[:15])}\n")
                log.write("\n")

    print(f"\n  Results saved to {logfile}")
    print("  Done.")


if __name__ == "__main__":
    main()
