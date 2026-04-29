#!/usr/bin/env python3
"""
XN297 Descramble Scanner for the Jasco QOBRGBXYZA remote.

The remote's RF daughter board likely uses a Panchip XN297L transceiver,
which is wire-compatible with the nRF24L01+ but applies a fixed XOR
scramble to the preamble, address, and payload before transmission.

This script:
  1. Captures raw packets on channels 21, 42, and 64 using the nRF24L01+
     in promiscuous mode (same raw SPI driver as rf_scanner.py).
  2. Applies the known XN297 descramble table to recover the real address
     and payload bytes.
  3. Looks for consistent descrambled patterns across button presses.

The XN297 scramble table is well-documented in the DeviationTX project
and several reverse-engineering write-ups.

Usage:
    python3 xn297_descramble.py
    python3 xn297_descramble.py --channel 42
    python3 xn297_descramble.py --addr-len 5
"""

import time
import argparse
import board
import busio
import digitalio


# ── XN297 Scramble Table ──
# The XN297 XORs the on-air bytes with this sequence. Byte 0 of the
# sequence corresponds to the first byte after the preamble (i.e., the
# first address byte). The table continues through the payload.
XN297_SCRAMBLE = [
    0xE3, 0xB1, 0x4B, 0x72, 0xC3, 0x17, 0x65, 0x20,
    0xE9, 0x25, 0x24, 0xC0, 0x13, 0x09, 0xB1, 0x57,
    0x66, 0x00, 0x21, 0x21, 0xAB, 0xF5, 0xD5, 0x39,
    0xBA, 0xA5, 0xE0, 0x76, 0x21, 0x08, 0x3F, 0x22,
    0xFE, 0x45, 0x68, 0x8C, 0x11, 0xC2, 0x8F, 0xD4,
    0x2C,
]

# The XN297 also bit-reverses each byte compared to nRF24L01+.
# nRF24L01+ transmits/receives MSBit first; XN297 convention is LSBit
# first for address bytes. We handle this with a bit-reverse LUT.
BIT_REVERSE = bytes([
    int(f"{i:08b}"[::-1], 2) for i in range(256)
])

# The standard nRF24L01+ preamble is 0x55 or 0xAA. The XN297 scrambles
# the preamble too, but since we're using the nRF24L01+ receiver with
# address matching, the preamble is already consumed by the radio. We
# only need to descramble from the address bytes onward.


class NRF24L01:
    """Minimal direct-SPI driver for the nRF24L01+ in promiscuous RX mode."""

    CONFIG      = 0x00
    EN_AA       = 0x01
    EN_RXADDR   = 0x02
    SETUP_AW    = 0x03
    RF_CH       = 0x05
    RF_SETUP    = 0x06
    STATUS      = 0x07
    RX_ADDR_P0  = 0x0A
    RX_ADDR_P1  = 0x0B
    RX_PW_P0    = 0x11
    RX_PW_P1    = 0x12
    FIFO_STATUS = 0x17
    DYNPD       = 0x1C
    FEATURE     = 0x1D

    R_RX_PAYLOAD = 0x61
    FLUSH_TX     = 0xE1
    FLUSH_RX     = 0xE2
    NOP          = 0xFF

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

        if not self._verify():
            raise RuntimeError("nRF24L01+ not responding on SPI")

    def _verify(self):
        val = self._read_reg(self.CONFIG)
        return val == 0x08 or val == 0x0E

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
        tx = bytearray([self.R_RX_PAYLOAD] + [0xFF] * length)
        rx = bytearray(len(tx))
        self._spi.write_readinto(tx, rx)
        self._csn.value = True
        self._spi.unlock()
        return rx[1:]

    def configure_promiscuous(self):
        self._ce.value = False
        self._write_reg(self.CONFIG, 0x03)
        time.sleep(0.002)
        self._write_reg(self.EN_AA, 0x00)
        self._write_reg(self.EN_RXADDR, 0x03)
        self._write_reg(self.SETUP_AW, 0x00)
        self._write_reg_bytes(self.RX_ADDR_P0, b"\x00\x55")
        self._write_reg_bytes(self.RX_ADDR_P1, b"\x00\xAA")
        self._write_reg(self.RX_PW_P0, 32)
        self._write_reg(self.RX_PW_P1, 32)
        self._write_reg(self.RF_SETUP, 0x07)
        self._write_reg(self.DYNPD, 0x00)
        self._write_reg(self.FEATURE, 0x00)
        self._command(self.FLUSH_RX)
        self._command(self.FLUSH_TX)
        self._write_reg(self.STATUS, 0x70)

    def set_channel(self, ch):
        self._ce.value = False
        self._write_reg(self.RF_CH, ch)
        self._ce.value = True

    def available(self):
        fifo = self._read_reg(self.FIFO_STATUS)
        return not bool(fifo & 0x01)

    def read(self):
        data = self._read_payload(32)
        self._write_reg(self.STATUS, 0x40)
        return data

    def start_listening(self):
        self._ce.value = True

    def stop_listening(self):
        self._ce.value = False

    def power_down(self):
        self._ce.value = False
        self._write_reg(self.CONFIG, 0x00)


def descramble_xn297(raw_bytes, addr_len=5):
    """
    Apply the XN297 XOR descramble to raw captured bytes.

    The nRF24L01+ in promiscuous mode delivers the 2-byte address match
    followed by the 32-byte "payload." The actual XN297 frame starts at
    the address. Since we matched on 0x0055 or 0x00AA (which are the
    scrambled preamble tails), the captured 32 bytes contain the
    scrambled address + payload of the XN297 frame.

    Returns: (address_bytes, payload_bytes, descrambled_full)
    """
    descrambled = bytearray(len(raw_bytes))
    for i in range(min(len(raw_bytes), len(XN297_SCRAMBLE))):
        descrambled[i] = raw_bytes[i] ^ XN297_SCRAMBLE[i]

    for i in range(len(XN297_SCRAMBLE), len(raw_bytes)):
        descrambled[i] = raw_bytes[i]

    addr = descrambled[:addr_len]
    payload = descrambled[addr_len:]

    return bytes(addr), bytes(payload), bytes(descrambled)


def descramble_bitrev(raw_bytes, addr_len=5):
    """
    Same as descramble_xn297 but also bit-reverses each byte.
    Some XN297 implementations use LSBit-first byte ordering.
    """
    reversed_raw = bytearray(BIT_REVERSE[b] for b in raw_bytes)
    return descramble_xn297(reversed_raw, addr_len)


def format_hex(data):
    return " ".join(f"{b:02X}" for b in data)


def main():
    parser = argparse.ArgumentParser(
        description="XN297 descramble scanner for Jasco QOBRGBXYZA remote"
    )
    parser.add_argument(
        "--channel", type=int, default=None,
        help="Listen on one channel instead of cycling 21/42/64."
    )
    parser.add_argument(
        "--addr-len", type=int, default=5, choices=[3, 4, 5],
        help="Assumed XN297 address length (default: 5)."
    )
    parser.add_argument(
        "--dwell", type=int, default=50,
        help="Dwell time per channel in ms (default: 50)."
    )
    parser.add_argument(
        "--bitrev", action="store_true",
        help="Also show bit-reversed descramble (LSBit-first mode)."
    )
    args = parser.parse_args()

    channels = [21, 42, 64] if args.channel is None else [args.channel]

    print("=" * 70)
    print("  XN297 Descramble Scanner — nRF24L01+ on Raspberry Pi 5")
    print("  Target: Jasco QOBRGBXYZA Remote")
    print("=" * 70)
    print(f"  Channels    : {channels}")
    print(f"  Addr length : {args.addr_len}")
    print(f"  Dwell       : {args.dwell} ms/channel")
    print(f"  Bit-reverse : {'yes' if args.bitrev else 'no'}")
    print("=" * 70)
    print()

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)

    radio = NRF24L01(spi, csn, ce)
    radio.configure_promiscuous()

    print("Radio configured. Press buttons on the remote.")
    print("Look for consistent descrambled addresses/payloads.\n")

    seen_addresses = {}
    packet_count = 0

    try:
        while True:
            for ch in channels:
                radio.set_channel(ch)
                deadline = time.monotonic() + (args.dwell / 1000.0)

                while time.monotonic() < deadline:
                    if radio.available():
                        raw = radio.read()
                        packet_count += 1
                        ts = time.strftime("%H:%M:%S")

                        addr, payload, full = descramble_xn297(
                            raw, args.addr_len
                        )
                        addr_hex = format_hex(addr)

                        seen_addresses[addr_hex] = (
                            seen_addresses.get(addr_hex, 0) + 1
                        )

                        print(f"[{ts}] #{packet_count:<5} ch={ch:>3}")
                        print(f"  RAW : {format_hex(raw)}")
                        print(f"  DESC: addr=[{addr_hex}]  "
                              f"payload=[{format_hex(payload[:16])}] ...")

                        if args.bitrev:
                            addr_r, pay_r, _ = descramble_bitrev(
                                raw, args.addr_len
                            )
                            print(f"  BREV: addr=[{format_hex(addr_r)}]  "
                                  f"payload=[{format_hex(pay_r[:16])}] ...")

                        print()

            if packet_count > 0 and packet_count % 20 == 0:
                print("-" * 70)
                print("Address frequency (descrambled):")
                for a, cnt in sorted(
                    seen_addresses.items(), key=lambda x: -x[1]
                ):
                    print(f"  {a}  seen {cnt}x")
                print("-" * 70)
                print()

    except KeyboardInterrupt:
        print(f"\n\nStopped. Total packets: {packet_count}")
        if seen_addresses:
            print("\nFinal address frequency (descrambled):")
            for a, cnt in sorted(
                seen_addresses.items(), key=lambda x: -x[1]
            ):
                print(f"  {a}  seen {cnt}x")
            top = max(seen_addresses.items(), key=lambda x: x[1])
            print(f"\nMost frequent address: {top[0]} ({top[1]} hits)")
            print("If this address is consistent across button presses,")
            print("it is likely the remote's real XN297 address.")
    finally:
        radio.power_down()
        print("Radio powered down.")


if __name__ == "__main__":
    main()
