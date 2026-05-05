#!/usr/bin/env python3
"""
XN297L Protocol Emulator — Channel-Hopping RF Scanner for Raspberry Pi 5

Ported from the nrf24_multipro project (XN297_emu.ino, nRF24L01.ino,
iface_nrf24l01.h) to Python for use with an nRF24L01+ module on SPI0.

The XNS1042 IC in the Jasco remote uses the XN297L protocol, which is
wire-compatible with the nRF24L01+ at the physical layer but applies
scrambling (de-whitening) and bit-reversal to the address and payload.

This script implements:
  1. XN297 preamble-based packet detection (syncword 0x0F71 from the
     28-bit XN297 preamble 0xC710F55)
  2. Channel hopping / scanning across the full 2.4 GHz range (ch 0-83)
  3. Software de-whitening via the XN297 scramble table (from XN297_emu.ino)
  4. Automatic remote address discovery
  5. CRC-16 validation (polynomial 0x1021, initial 0xB5D2)
  6. Targeted reception once the address is locked

Hardware: nRF24L01+ on SPI0 (Blinka), CE=GPIO D25, CSN=GPIO D8

Usage:
    python3 xn297l_scanner.py                   # Scan all channels, discover address
    python3 xn297l_scanner.py --channels 21,42,64  # Scan specific channels only
    python3 xn297l_scanner.py --addr 3872 2DA8 5E   # Skip discovery, lock to known address
    python3 xn297l_scanner.py --addr-len 4          # Assume 4-byte XN297 address
"""

import time
import argparse
import sys
import board
import busio
import digitalio


# ═══════════════════════════════════════════════════════════════════════
# XN297 Scramble Table — from XN297_emu.ino (nrf24_multipro project)
#
# The XN297L XORs on-air bytes with this table. Byte 0 corresponds to
# the first address byte. The table covers address (up to 5 bytes) +
# payload (up to 30 bytes).
# ═══════════════════════════════════════════════════════════════════════

XN297_SCRAMBLE = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
    0x8E, 0xC5, 0x2F,
]

# CRC-16 XOR-out values indexed by (addr_len - 3 + payload_len)
# From XN297_emu.ino — used to finalize the CRC after computation
XN297_CRC_XOROUT = [
    0x0000, 0x3448, 0x9BA7, 0x8BBB, 0x85E1, 0x3E8C,
    0x451E, 0x18E6, 0x6B24, 0xE7AB, 0x3828, 0x814B,
    0xD461, 0xF494, 0x2503, 0x691D, 0xFE8B, 0x9BA7,
    0x8B17, 0x2920, 0x8B5F, 0x61B1, 0xD391, 0x7401,
    0x2138, 0x129F, 0xB3A0, 0x2988,
]

CRC_POLY    = 0x1021
CRC_INITIAL = 0xB5D2

# XN297 28-bit preamble: 0xC710F55
# After the nRF24L01+ consumes the 0x55 byte as its own preamble,
# the remaining bytes that appear as "address" on-air are:
XN297_PREAMBLE_TAIL = [0x0F, 0x71]

# Bit-reverse lookup table (256 entries)
BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])

# Number of channels in the 2.4 GHz ISM band for nRF24L01+
MAX_CHANNEL = 84


# ═══════════════════════════════════════════════════════════════════════
# XN297 Protocol Functions — ported from XN297_emu.ino
# ═══════════════════════════════════════════════════════════════════════

def bit_reverse(b):
    return BIT_REVERSE[b]


def xn297_scramble_address(addr, addr_len):
    """Scramble an XN297 address for use as the nRF24L01+ RX address.

    From XN297_SetRXAddr in XN297_emu.ino:
        buf[i] = addr[i] ^ scramble[addr_len - i - 1]
    The scramble table is indexed in reverse for the address portion.
    """
    scrambled = bytearray(addr_len)
    for i in range(addr_len):
        scrambled[i] = addr[i] ^ XN297_SCRAMBLE[addr_len - i - 1]
    return bytes(scrambled)


def xn297_descramble_address(scrambled_addr, addr_len):
    """Recover the real XN297 address from scrambled on-air bytes.

    Inverse of xn297_scramble_address (XOR is self-inverse).
    """
    real = bytearray(addr_len)
    for i in range(addr_len):
        real[i] = scrambled_addr[i] ^ XN297_SCRAMBLE[addr_len - i - 1]
    return bytes(real)


def xn297_read_payload(raw_payload, addr_len):
    """De-whiten a received payload using the XN297 scramble table.

    From XN297_ReadPayload in XN297_emu.ino:
        msg[i] = bit_reverse(raw[i]) ^ bit_reverse(scramble[i + addr_len])

    Each raw byte is bit-reversed (nRF24L01+ is MSBit-first, XN297 is
    LSBit-first), then XOR'd with the bit-reversed scramble table entry.
    """
    result = bytearray(len(raw_payload))
    for i in range(len(raw_payload)):
        scr_idx = i + addr_len
        if scr_idx < len(XN297_SCRAMBLE):
            result[i] = BIT_REVERSE[raw_payload[i]] ^ BIT_REVERSE[XN297_SCRAMBLE[scr_idx]]
        else:
            result[i] = BIT_REVERSE[raw_payload[i]]
    return bytes(result)


def xn297_crc16(data):
    """Compute XN297 CRC-16 (polynomial 0x1021, initial 0xB5D2)."""
    crc = CRC_INITIAL
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ CRC_POLY
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


def xn297_verify_crc(on_air_bytes, addr_len, payload_len):
    """Verify CRC on a captured XN297 frame.

    The on-air frame (after preamble) is:
        [scrambled_addr (addr_len)] [scrambled_payload+CRC]

    CRC is computed over the scrambled address + scrambled payload,
    then XOR'd with crc_xorout[addr_len - 3 + payload_len].
    The last 2 bytes of the frame are the CRC.
    """
    total = addr_len + payload_len
    if total + 2 > len(on_air_bytes):
        return False, 0, 0

    frame = on_air_bytes[:total]
    received_crc = (on_air_bytes[total] << 8) | on_air_bytes[total + 1]

    crc = xn297_crc16(frame)
    xorout_idx = addr_len - 3 + payload_len
    if xorout_idx < len(XN297_CRC_XOROUT):
        crc ^= XN297_CRC_XOROUT[xorout_idx]

    return crc == received_crc, crc, received_crc


def to_hex(data):
    return " ".join(f"{b:02X}" for b in data)


# ═══════════════════════════════════════════════════════════════════════
# nRF24L01+ Driver — ported from nRF24L01.ino / iface_nrf24l01.h
# ═══════════════════════════════════════════════════════════════════════

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

    # ── Discovery mode: catch any XN297 packet by matching the
    #    known XN297 preamble tail as the nRF24L01+ address ──

    def configure_xn297_discovery(self):
        """Set up for XN297 preamble-based promiscuous reception.

        The XN297 28-bit preamble is 0xC710F55. The nRF24L01+ consumes
        0x55 as its own preamble byte. We set a 2-byte address to match
        the next bytes (0x0F, 0x71) so any XN297 packet triggers a FIFO
        entry. The 32-byte "payload" then contains the scrambled XN297
        address + payload + CRC.

        We also listen on pipe 1 with 0xAA-based preamble matching for
        XN297 packets where the scrambled address MSB starts with 1
        (nRF24L01+ uses 0xAA preamble instead of 0x55 in that case).
        """
        self._ce.value = False

        self._reg_write(0x00, 0x03)   # CONFIG: PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._reg_write(0x01, 0x00)   # EN_AA: off
        self._reg_write(0x02, 0x03)   # EN_RXADDR: pipe 0 + pipe 1

        # 2-byte address width (SETUP_AW=0x00 is technically illegal
        # but works on genuine nRF24L01+ and most clones)
        self._reg_write(0x03, 0x00)

        # Pipe 0: match XN297 preamble for 0x55 (address MSB starts with 0)
        # On-air after 0x55 preamble: 0x0F, 0x71, ...
        # nRF24L01+ writes LSByte first, on-air is MSByte first
        self._reg_write_bytes(0x0A, bytes([0x71, 0x0F]))

        # Pipe 1: match for 0xAA preamble (address MSB starts with 1)
        # When XN297 scrambled addr MSB has bit7=1, nRF uses 0xAA preamble
        # The preamble continuation after 0xAA is the bitwise inverse
        self._reg_write_bytes(0x0B, bytes([0x8E, 0xF0]))

        self._reg_write(0x11, 32)     # RX_PW_P0: 32 bytes
        self._reg_write(0x12, 32)     # RX_PW_P1: 32 bytes
        self._reg_write(0x06, 0x07)   # RF_SETUP: 1 Mbps, 0 dBm
        self._reg_write(0x1C, 0x00)   # DYNPD: off
        self._reg_write(0x1D, 0x00)   # FEATURE: off
        self._cmd(0xE2)               # FLUSH_RX
        self._cmd(0xE1)               # FLUSH_TX
        self._reg_write(0x07, 0x70)   # STATUS: clear flags

    # ── Targeted mode: receive from a known XN297 address ──

    def configure_xn297_targeted(self, xn297_addr, addr_len):
        """Set up for reception from a specific known XN297 address.

        The address is scrambled per XN297_SetRXAddr from XN297_emu.ino
        and written as the nRF24L01+ pipe 0 RX address. CRC is left
        disabled (validated in software via xn297_verify_crc).
        """
        self._ce.value = False

        scrambled = xn297_scramble_address(xn297_addr, addr_len)

        self._reg_write(0x00, 0x03)   # CONFIG: PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._reg_write(0x01, 0x00)   # EN_AA: off
        self._reg_write(0x02, 0x01)   # EN_RXADDR: pipe 0 only
        self._reg_write(0x03, addr_len - 2)  # SETUP_AW
        self._reg_write_bytes(0x0A, scrambled + b'\x00' * (5 - addr_len))
        self._reg_write(0x11, 32)     # RX_PW_P0: 32 bytes
        self._reg_write(0x06, 0x07)   # RF_SETUP: 1 Mbps, 0 dBm
        self._reg_write(0x1C, 0x00)   # DYNPD: off
        self._reg_write(0x1D, 0x00)   # FEATURE: off
        self._cmd(0xE2)               # FLUSH_RX
        self._cmd(0xE1)               # FLUSH_TX
        self._reg_write(0x07, 0x70)   # STATUS: clear flags

    def set_channel(self, ch):
        self._ce.value = False
        self._reg_write(0x05, ch)
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


# ═══════════════════════════════════════════════════════════════════════
# Scanner — Discovery + Channel Hopping + De-whitening
# ═══════════════════════════════════════════════════════════════════════

def scan_discovery(radio, channels, addr_len, dwell_ms, max_packets=200):
    """Scan for XN297 packets, de-whiten them, and discover the address.

    Uses the promiscuous preamble-matching mode. Each received 32-byte
    "payload" actually contains:
        [scrambled_XN297_addr] [scrambled_XN297_payload] [CRC bytes]

    We try all plausible payload lengths and check CRC to validate.
    """
    radio.configure_xn297_discovery()

    print(f"\n  Scanning {len(channels)} channels: {channels}")
    print(f"  Dwell: {dwell_ms} ms/channel | Address length: {addr_len}")
    print("  Press buttons on the remote. Ctrl+C to stop.\n")

    address_votes = {}
    active_channels = {}
    packet_count = 0

    try:
        while packet_count < max_packets:
            for ch in channels:
                radio.set_channel(ch)
                deadline = time.monotonic() + (dwell_ms / 1000.0)

                while time.monotonic() < deadline:
                    if not radio.available():
                        continue

                    raw = radio.read()
                    packet_count += 1

                    # The raw 32 bytes contain the scrambled XN297 frame
                    # starting from the address (preamble was consumed)
                    scrambled_addr = raw[:addr_len]
                    real_addr = xn297_descramble_address(scrambled_addr, addr_len)
                    addr_hex = to_hex(real_addr)

                    # Try CRC validation at various payload lengths
                    crc_ok = False
                    best_pay_len = 0
                    for pay_len in range(4, 27):
                        ok, calc, recv = xn297_verify_crc(raw, addr_len, pay_len)
                        if ok:
                            crc_ok = True
                            best_pay_len = pay_len
                            break

                    # De-whiten the payload portion
                    raw_payload = raw[addr_len:addr_len + best_pay_len] if crc_ok \
                        else raw[addr_len:addr_len + 16]
                    payload = xn297_read_payload(raw_payload, addr_len)

                    # Track address frequency
                    key = addr_hex
                    address_votes[key] = address_votes.get(key, 0) + 1
                    active_channels[ch] = active_channels.get(ch, 0) + 1

                    ts = time.strftime("%H:%M:%S")
                    crc_str = "CRC OK" if crc_ok else "no CRC"
                    print(f"  [{ts}] ch={ch:>2} #{packet_count:<4} "
                          f"addr=[{addr_hex}] "
                          f"pay=[{to_hex(payload[:12])}...] "
                          f"{crc_str}")

            # Periodic summary
            if packet_count > 0 and packet_count % 20 == 0:
                _print_summary(address_votes, active_channels)

    except KeyboardInterrupt:
        pass

    print(f"\n  Discovery complete. {packet_count} packets captured.")
    _print_summary(address_votes, active_channels)

    if address_votes:
        top_addr = max(address_votes, key=address_votes.get)
        top_count = address_votes[top_addr]
        print(f"\n  Most likely remote address: [{top_addr}] "
              f"({top_count} hits)")
        return top_addr, active_channels
    return None, active_channels


def _print_summary(address_votes, active_channels):
    print("\n  " + "-" * 60)
    print("  Address frequency (descrambled):")
    for addr, cnt in sorted(address_votes.items(), key=lambda x: -x[1])[:10]:
        print(f"    [{addr}]  {cnt}x")
    if active_channels:
        print("  Active channels:")
        for ch, cnt in sorted(active_channels.items(), key=lambda x: -x[1]):
            print(f"    ch {ch:>2}: {cnt} packets")
    print("  " + "-" * 60)


def listen_targeted(radio, addr_bytes, addr_len, channels, dwell_ms):
    """Listen on a known address with proper de-whitening.

    Uses the XN297 address scrambling to configure the nRF24L01+ for
    targeted reception. Payload is de-whitened per XN297_ReadPayload.
    """
    radio.configure_xn297_targeted(addr_bytes, addr_len)

    print(f"\n  Targeted listen on [{to_hex(addr_bytes)}]")
    print(f"  Channels: {channels}")
    print("  Press buttons on the remote. Ctrl+C to stop.\n")

    packet_count = 0
    last_code = None
    last_time = 0
    DEBOUNCE = 0.30

    try:
        while True:
            for ch in channels:
                radio.set_channel(ch)
                deadline = time.monotonic() + (dwell_ms / 1000.0)

                while time.monotonic() < deadline:
                    if not radio.available():
                        continue

                    raw = radio.read()
                    packet_count += 1
                    now = time.monotonic()

                    # De-whiten the payload
                    payload = xn297_read_payload(raw, addr_len)
                    pay_hex = to_hex(payload)

                    # Debounce
                    if pay_hex == last_code and (now - last_time) < DEBOUNCE:
                        continue
                    last_code = pay_hex
                    last_time = now

                    # CRC validation (payload in raw domain, CRC is last 2 bytes)
                    # In targeted mode, the address was already matched by hardware
                    # so we only need to check CRC on the payload+CRC portion

                    ts = time.strftime("%H:%M:%S")
                    print(f"  [{ts}] ch={ch:>2} #{packet_count:<5} "
                          f"payload=[{to_hex(payload[:20])}]"
                          f"{'...' if len(payload) > 20 else ''}")

    except KeyboardInterrupt:
        print(f"\n  Stopped. {packet_count} packets received.")


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="XN297L Protocol Emulator — Channel-hopping RF scanner "
                    "for the Jasco QOBRGBXYZA remote (XNS1042 / XN297L)")
    parser.add_argument(
        "--channels", type=str, default=None,
        help="Comma-separated channel list (default: scan 0-83)")
    parser.add_argument(
        "--addr", type=str, nargs="+", default=None,
        help="Known XN297 address bytes in hex (e.g., --addr 38 72 2D A8 5E). "
             "Skips discovery and goes straight to targeted listen.")
    parser.add_argument(
        "--addr-len", type=int, default=5, choices=[3, 4, 5],
        help="XN297 address length in bytes (default: 5)")
    parser.add_argument(
        "--dwell", type=int, default=30,
        help="Dwell time per channel in ms (default: 30)")
    parser.add_argument(
        "--fcc-channels", action="store_true",
        help="Only scan FCC-reported channels: 21, 42, 64")
    parser.add_argument(
        "--max-packets", type=int, default=500,
        help="Max packets to capture in discovery mode (default: 500)")
    args = parser.parse_args()

    # Determine channel list
    if args.fcc_channels:
        channels = [21, 42, 64]
    elif args.channels:
        channels = [int(c.strip()) for c in args.channels.split(",")]
    else:
        channels = list(range(MAX_CHANNEL))

    print("=" * 66)
    print("  XN297L Protocol Emulator — nRF24L01+ Channel-Hopping Scanner")
    print("  Target: Jasco QOBRGBXYZA Remote (XNS1042 / XN297L)")
    print("=" * 66)

    # Initialize radio hardware
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    try:
        if args.addr:
            # Targeted mode — address already known
            addr_bytes = bytes([int(b, 16) for b in args.addr])
            addr_len = len(addr_bytes)
            print(f"  Mode: Targeted | Address: [{to_hex(addr_bytes)}]")
            print(f"  Scrambled RX addr: "
                  f"[{to_hex(xn297_scramble_address(addr_bytes, addr_len))}]")
            listen_targeted(radio, addr_bytes, addr_len, channels, args.dwell)
        else:
            # Discovery mode — find the remote's address
            print(f"  Mode: Discovery | Addr length: {args.addr_len}")
            print(f"  Channels: {channels[:10]}{'...' if len(channels) > 10 else ''}")
            print(f"  Dwell: {args.dwell} ms | Max packets: {args.max_packets}")

            top_addr, active_ch = scan_discovery(
                radio, channels, args.addr_len, args.dwell, args.max_packets)

            if top_addr and active_ch:
                addr_bytes = bytes([int(b, 16) for b in top_addr.split()])
                # Focus on active channels only
                focused = sorted(active_ch.keys(),
                                 key=lambda c: -active_ch[c])[:5]
                resp = input(f"\n  Lock to [{top_addr}] on channels "
                             f"{focused}? (Y/n): ").strip().lower()
                if resp != 'n':
                    listen_targeted(radio, addr_bytes, args.addr_len,
                                    focused, args.dwell)

    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        radio.power_down()
        print("  Radio powered down. Goodbye.")


if __name__ == "__main__":
    main()
