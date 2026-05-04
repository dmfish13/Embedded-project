#!/usr/bin/env python3
"""
BK2423 / XN297 address discovery scanner for the Jasco remote.

The remote uses a Beken BK2423 (or compatible) transceiver at 1 Mbps GFSK
on channels 21, 42, 64. This scanner tries many address + descramble
combinations to find the correct one.

Tested configurations:
  - 12 common addresses (nRF24 defaults, BK2421 patterns, Chinese product defaults)
  - 2 scramble tables (XN297 Table A, XN297 Table B)
  - With and without bit reversal
  - With and without scrambling (BK2423 compatible mode)
  - 3, 4, and 5-byte address widths

Hold the POWER button during the entire test (~3 minutes).

Usage:
    python3 rf_scanner_reset.py
    python3 rf_bk2423_scanner.py
"""

import time
import board
import busio
import digitalio


BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])

# XN297 scramble table A (from DeviationTX / xn297_descramble.py)
SCRAMBLE_A = [
    0xE3, 0xB1, 0x4B, 0x72, 0xC3, 0x17, 0x65, 0x20,
    0xE9, 0x25, 0x24, 0xC0, 0x13, 0x09, 0xB1, 0x57,
    0x66, 0x00, 0x21, 0x21, 0xAB, 0xF5, 0xD5, 0x39,
    0xBA, 0xA5, 0xE0, 0x76, 0x21, 0x08, 0x3F, 0x22,
]

# XN297 scramble table B (alternate, from other RE projects)
SCRAMBLE_B = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
]

# Common addresses used in Chinese 2.4 GHz products
COMMON_ADDRS = [
    ([0x00, 0x00, 0x00, 0x00, 0x00], "zeros"),
    ([0xE7, 0xE7, 0xE7, 0xE7, 0xE7], "nRF24 default P0"),
    ([0xC2, 0xC2, 0xC2, 0xC2, 0xC2], "nRF24 default P1"),
    ([0x12, 0x34, 0x56, 0x78, 0x9A], "sequential"),
    ([0xAA, 0x55, 0xAA, 0x55, 0xAA], "AA55 pattern"),
    ([0x55, 0xAA, 0x55, 0xAA, 0x55], "55AA pattern"),
    ([0x71, 0x0C, 0x00, 0x00, 0x00], "BK2421 common"),
    ([0x01, 0x02, 0x03, 0x04, 0x05], "incremental"),
    ([0xA5, 0xA5, 0xA5, 0xA5, 0xA5], "A5 fill"),
    ([0xFF, 0xFF, 0xFF, 0xFF, 0xFF], "all ones"),
    ([0x66, 0x88, 0x66, 0x88, 0x66], "6688 pattern"),
    ([0xAB, 0xCD, 0xEF, 0x01, 0x23], "ABCDEF"),
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

    def configure(self, channel, addr_bytes, addr_width):
        self._ce.value = False
        self._write_reg(0x00, 0x03)  # PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)  # no auto-ack
        self._write_reg(0x02, 0x01)  # enable pipe 0 only
        self._write_reg(0x03, max(addr_width - 2, 0x01))  # address width
        self._write_reg_bytes(0x0A, bytes(addr_bytes[:addr_width]))
        self._write_reg(0x11, 32)    # 32-byte payload
        self._write_reg(0x06, 0x07)  # 1 Mbps, 0 dBm
        self._write_reg(0x1C, 0x00)  # no dynamic payload
        self._write_reg(0x1D, 0x00)  # no features
        self._command(0xE2)  # flush RX
        self._command(0xE1)  # flush TX
        self._write_reg(0x07, 0x70)  # clear flags
        self._write_reg(0x05, channel)
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


def scramble_addr(addr, table, bitrev=False):
    """Apply XOR scramble to an address. Optionally bit-reverse each byte."""
    result = []
    for i, b in enumerate(addr):
        scrambled = b ^ table[i]
        if bitrev:
            scrambled = BIT_REVERSE[scrambled]
        result.append(scrambled)
    return result


def descramble_payload(payload, table, offset, bitrev=False):
    """Descramble a payload using the given table starting at offset."""
    result = bytearray(len(payload))
    for i, b in enumerate(payload):
        if bitrev:
            b = BIT_REVERSE[b]
        idx = offset + i
        if idx < len(table):
            result[i] = b ^ table[idx]
        else:
            result[i] = b
    return result


def test_config(radio, channel, addr_bytes, addr_width, seconds=2):
    """Test a single address configuration. Returns (packet_count, sample_payloads)."""
    radio.configure(channel, addr_bytes, addr_width)
    packets = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            packets.append(bytes(raw))
    return len(packets), packets[:5]


def check_consistency(packets):
    """Check if captured payloads share common byte values at any position."""
    if len(packets) < 3:
        return 0
    consistent = 0
    for pos in range(min(16, len(packets[0]))):
        values = [p[pos] for p in packets]
        from collections import Counter
        top_val, top_count = Counter(values).most_common(1)[0]
        if top_count / len(packets) > 0.5:
            consistent += 1
    return consistent


def main():
    print("=" * 72)
    print("  BK2423 / XN297 Address Discovery Scanner")
    print("  Target: Jasco QOBRGBXYZA Remote on ch 42 @ 1 Mbps")
    print("=" * 72)
    print()
    print("  HOLD the POWER button continuously during the ENTIRE test.")
    print("  Do NOT release until 'Done' appears.")
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    channel = 42
    hits = []
    test_num = 0

    # Build all configurations to test
    configs = []

    for addr, name in COMMON_ADDRS:
        for width in [5, 4, 3]:
            a = addr[:width]

            # Mode 1: No scramble (BK2423 compatible mode)
            configs.append({
                "addr": a, "width": width,
                "label": f"RAW {name} {width}B",
                "scramble": None, "bitrev": False,
            })

            # Mode 2: No scramble, bit-reversed
            rev_a = [BIT_REVERSE[b] for b in a]
            configs.append({
                "addr": rev_a, "width": width,
                "label": f"RAW+BREV {name} {width}B",
                "scramble": None, "bitrev": True,
            })

            # Mode 3: XN297 scramble table A
            scr_a = scramble_addr(a, SCRAMBLE_A[:width])
            configs.append({
                "addr": scr_a, "width": width,
                "label": f"SCR_A {name} {width}B",
                "scramble": "A", "bitrev": False,
            })

            # Mode 4: XN297 scramble table A + bit reversal
            scr_a_rev = scramble_addr(a, SCRAMBLE_A[:width], bitrev=True)
            configs.append({
                "addr": scr_a_rev, "width": width,
                "label": f"SCR_A+BREV {name} {width}B",
                "scramble": "A", "bitrev": True,
            })

            # Mode 5: XN297 scramble table B
            scr_b = scramble_addr(a, SCRAMBLE_B[:width])
            configs.append({
                "addr": scr_b, "width": width,
                "label": f"SCR_B {name} {width}B",
                "scramble": "B", "bitrev": False,
            })

            # Mode 6: XN297 scramble table B + bit reversal
            scr_b_rev = scramble_addr(a, SCRAMBLE_B[:width], bitrev=True)
            configs.append({
                "addr": scr_b_rev, "width": width,
                "label": f"SCR_B+BREV {name} {width}B",
                "scramble": "B", "bitrev": True,
            })

    # Also add the promiscuous baseline for comparison
    configs.insert(0, {
        "addr": [0x00, 0x55], "width": 2,
        "label": "PROMISCUOUS 0x0055",
        "scramble": None, "bitrev": False,
    })
    configs.insert(1, {
        "addr": [0x00, 0xAA], "width": 2,
        "label": "PROMISCUOUS 0x00AA",
        "scramble": None, "bitrev": False,
    })

    total = len(configs)
    print(f"  Testing {total} configurations on channel {channel}")
    print(f"  Estimated time: ~{total * 2 // 60} min {total * 2 % 60} sec")
    print()

    # Noise baseline first
    input("  Press Enter for 3-second NOISE baseline (don't press remote)...")
    noise_count, _ = test_config(radio, channel, [0x00, 0x55], 2, seconds=3)
    print(f"  Noise baseline: {noise_count} packets in 3 seconds\n")

    input("  Now HOLD the POWER button and press Enter...")
    print()

    for i, cfg in enumerate(configs):
        test_num += 1
        count, samples = test_config(radio, channel, cfg["addr"], cfg["width"], seconds=2)

        addr_hex = " ".join(f"{b:02X}" for b in cfg["addr"])

        if count > noise_count + 3:
            consistent = check_consistency(samples) if samples else 0
            marker = " <<<" if count > noise_count * 2 + 10 else ""
            star = " ***" if consistent >= 3 else ""
            print(f"  [{test_num:>3}/{total}] {cfg['label']:<35} "
                  f"addr=[{addr_hex}]  {count:>4} pkts  "
                  f"({consistent} consistent){marker}{star}")

            if count > noise_count * 2 + 10:
                hits.append({
                    "label": cfg["label"],
                    "addr": cfg["addr"],
                    "width": cfg["width"],
                    "count": count,
                    "consistent": consistent,
                    "samples": samples,
                    "scramble": cfg["scramble"],
                    "bitrev": cfg["bitrev"],
                })

                # Show sample payloads
                for s in samples[:2]:
                    hex_str = "".join(f"{b:02X}" for b in s[:16])
                    print(f"         sample: {hex_str}...")

        elif test_num % 50 == 0:
            print(f"  [{test_num:>3}/{total}] ... scanning ...")

    # Results
    print(f"\n{'='*72}")
    if hits:
        hits.sort(key=lambda x: (-x["consistent"], -x["count"]))
        print("  HITS (significantly above noise):")
        print(f"{'='*72}")
        for h in hits:
            addr_hex = " ".join(f"{b:02X}" for b in h["addr"])
            print(f"\n  {h['label']}")
            print(f"    Address: [{addr_hex}] ({h['width']}B)")
            print(f"    Packets: {h['count']} (noise: {noise_count})")
            print(f"    Consistent positions: {h['consistent']}/16")
            print(f"    Scramble: {h['scramble'] or 'none'}, "
                  f"BitRev: {h['bitrev']}")

            if h["samples"]:
                print("    Samples:")
                for s in h["samples"][:3]:
                    hex_str = "".join(f"{b:02X}" for b in s[:20])
                    print(f"      {hex_str}")

                    # Try descrambling
                    if h["scramble"]:
                        table = SCRAMBLE_A if h["scramble"] == "A" else SCRAMBLE_B
                        desc = descramble_payload(
                            s, table, h["width"], h["bitrev"]
                        )
                        desc_hex = "".join(f"{b:02X}" for b in desc[:20])
                        print(f"      -> descrambled: {desc_hex}")
    else:
        print("  NO HITS above noise threshold.")
        print(f"{'='*72}")
        print()
        print("  Possible causes:")
        print("    1. Remote not transmitting (check batteries, hold button)")
        print("    2. Address not in our common list (custom address)")
        print("    3. Different scramble table variant")
        print("    4. BK2423 using non-standard protocol features")
        print()
        print("  Next steps:")
        print("    - Try rf_capture.py to verify signal presence")
        print("    - Try xn297_descramble.py --channel 42 --bitrev")
        print("    - Consider SDR analysis for full protocol decode")

    try:
        radio.power_down()
    except Exception:
        pass
    print("\nDone.")


if __name__ == "__main__":
    main()
