#!/usr/bin/env python3
"""
RF Scanner for the Jasco QOBRGBXYZA remote -- direct SPI implementation.

Bypasses the circuitpython-nrf24l01 library entirely and communicates
with the nRF24L01+ via raw SPI register reads/writes. This avoids the
SPIDevice / SPIDevCtx wrapper issues on the Raspberry Pi 5.

Hardware wiring (nRF24L01+ -> Raspberry Pi 5):
    VCC  -> Pin 17  (3.3 V)
    GND  -> Pin 20  (GND)
    CE   -> GPIO 25 (Pin 22)
    CSN  -> GPIO 8  (Pin 24)
    SCK  -> GPIO 11 (Pin 23)
    MOSI -> GPIO 10 (Pin 19)
    MISO -> GPIO 9  (Pin 21)

Promiscuous mode settings:
    - CRC disabled
    - Auto-ACK disabled
    - 2-byte address width (minimum)
    - 32-byte fixed payload
    - 1 Mbps data rate

Usage:
    python3 rf_scanner.py
    python3 rf_scanner.py --channel 76
    python3 rf_scanner.py --start 60 --end 80
"""

import time
import argparse
import board
import busio
import digitalio


class NRF24L01:
    """Minimal direct-SPI driver for the nRF24L01+ in promiscuous RX mode."""

    # Register addresses
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

    # Commands
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
        """Check the radio responds by reading the default CONFIG value."""
        val = self._read_reg(self.CONFIG)
        return val == 0x08 or val == 0x0E

    def _read_reg(self, reg):
        """Read a single-byte register."""
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
        """Write a single-byte register."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg, value]))
        self._csn.value = True
        self._spi.unlock()

    def _write_reg_bytes(self, reg, data):
        """Write a multi-byte register."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0x20 | reg]) + bytearray(data))
        self._csn.value = True
        self._spi.unlock()

    def _command(self, cmd):
        """Send a single-byte command."""
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        buf = bytearray([cmd])
        self._spi.write(buf)
        self._csn.value = True
        self._spi.unlock()

    def _read_payload(self, length):
        """Read RX payload of given length."""
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
        """Set up the radio for promiscuous packet sniffing."""
        self._ce.value = False

        # Power up in RX mode, CRC disabled
        # CONFIG: EN_CRC=0, PWR_UP=1, PRIM_RX=1 -> 0x03
        self._write_reg(self.CONFIG, 0x03)
        time.sleep(0.002)

        # Disable auto-acknowledge on all pipes
        self._write_reg(self.EN_AA, 0x00)

        # Enable RX pipes 0 and 1
        self._write_reg(self.EN_RXADDR, 0x03)

        # Address width: 2 bytes (register value = width - 2)
        self._write_reg(self.SETUP_AW, 0x00)

        # Set RX addresses: generic patterns to catch broad traffic
        self._write_reg_bytes(self.RX_ADDR_P0, b"\x00\x55")
        self._write_reg_bytes(self.RX_ADDR_P1, b"\x00\xAA")

        # Fixed 32-byte payload on both pipes
        self._write_reg(self.RX_PW_P0, 32)
        self._write_reg(self.RX_PW_P1, 32)

        # 1 Mbps, 0 dBm PA, LNA enabled -> 0x07
        self._write_reg(self.RF_SETUP, 0x07)

        # Disable dynamic payloads
        self._write_reg(self.DYNPD, 0x00)
        self._write_reg(self.FEATURE, 0x00)

        # Flush FIFOs
        self._command(self.FLUSH_RX)
        self._command(self.FLUSH_TX)

        # Clear interrupt flags
        self._write_reg(self.STATUS, 0x70)

    def set_channel(self, ch):
        """Switch to a specific RF channel (0-125)."""
        self._ce.value = False
        self._write_reg(self.RF_CH, ch)
        self._ce.value = True

    def available(self):
        """Check if there is data in the RX FIFO."""
        fifo = self._read_reg(self.FIFO_STATUS)
        return not bool(fifo & 0x01)

    def read(self):
        """Read one 32-byte payload from the RX FIFO."""
        data = self._read_payload(32)
        # Clear RX_DR flag
        self._write_reg(self.STATUS, 0x40)
        return data

    def start_listening(self):
        """Enter RX mode."""
        self._ce.value = True

    def stop_listening(self):
        """Exit RX mode."""
        self._ce.value = False

    def power_down(self):
        """Power off the radio."""
        self._ce.value = False
        self._write_reg(self.CONFIG, 0x00)


def main():
    parser = argparse.ArgumentParser(
        description="nRF24L01+ RF scanner for the Jasco QOBRGBXYZA remote"
    )
    parser.add_argument(
        "--channel", type=int, default=None,
        help="Listen on a single channel (0-125) instead of sweeping."
    )
    parser.add_argument(
        "--start", type=int, default=0,
        help="Start channel for sweep (default: 0)."
    )
    parser.add_argument(
        "--end", type=int, default=125,
        help="End channel for sweep (default: 125)."
    )
    parser.add_argument(
        "--dwell", type=int, default=10,
        help="Dwell time per channel in ms (default: 10)."
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  nRF24L01+ RF Scanner -- Direct SPI (Pi 5)")
    print("  Jasco QOBRGBXYZA Remote")
    print("=" * 60)

    # Init SPI and pins
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)

    radio = NRF24L01(spi, csn, ce)
    radio.configure_promiscuous()

    if args.channel is not None:
        print(f"  Mode      : Fixed channel {args.channel} ({2400 + args.channel} MHz)")
    else:
        print(f"  Mode      : Sweep channels {args.start}-{args.end}")
        print(f"  Freq range: {2400 + args.start}-{2400 + args.end} MHz")

    print(f"  Dwell     : {args.dwell} ms/channel")
    print(f"  CRC       : disabled")
    print(f"  Auto-ACK  : disabled")
    print(f"  Addr width: 2 bytes")
    print(f"  Payload   : 32 bytes fixed")
    print(f"  Data rate : 1 Mbps")
    print("=" * 60)
    print("\nPress Ctrl+C to stop.\n")

    packet_count = 0
    round_num = 0

    try:
        if args.channel is not None:
            # Fixed channel mode
            radio.set_channel(args.channel)
            while True:
                if radio.available():
                    raw = radio.read()
                    packet_count += 1
                    hex_str = "".join(f"{b:02X}" for b in raw)[:20]
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] #{packet_count:<5} ch={args.channel:>3} | {hex_str}")
                time.sleep(0.001)
        else:
            # Sweep mode
            while True:
                round_num += 1
                hits_this_round = 0

                for ch in range(args.start, args.end + 1):
                    radio.set_channel(ch)
                    deadline = time.monotonic() + (args.dwell / 1000.0)

                    while time.monotonic() < deadline:
                        if radio.available():
                            raw = radio.read()
                            packet_count += 1
                            hits_this_round += 1
                            hex_str = "".join(f"{b:02X}" for b in raw)[:20]
                            ts = time.strftime("%H:%M:%S")
                            print(f"[{ts}] #{packet_count:<5} ch={ch:>3} "
                                  f"({2400+ch} MHz) | {hex_str}")

                if hits_this_round == 0 and round_num % 10 == 0:
                    print(f"  ... sweep round {round_num}, no packets yet")

    except KeyboardInterrupt:
        print(f"\n\nStopped. Total packets captured: {packet_count}")
    finally:
        radio.power_down()
        print("Radio powered down.")


if __name__ == "__main__":
    main()
