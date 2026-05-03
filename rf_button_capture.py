#!/usr/bin/env python3
"""
Systematic RF capture for all 25 Jasco QOBRGBXYZA remote buttons.

Captures 3 button presses per button on channels 21, 42, 64 at 1 Mbps.
Saves results to rf_capture_log.txt for analysis.

Usage:
    python3 rf_scanner_reset.py      # Reset radio first
    python3 rf_button_capture.py
"""

import time
import sys
import os
import board
import busio
import digitalio


class NRF24L01:
    """Minimal direct-SPI driver for the nRF24L01+."""

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
        """Configure radio for promiscuous sniffing at 1 Mbps."""
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
        self._write_reg(self.RF_SETUP, 0x07)  # 1 Mbps
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

    def power_down(self):
        self._ce.value = False
        self._write_reg(self.CONFIG, 0x00)


TARGET_CHANNELS = [21, 42, 64]

BUTTONS = [
    # Row 1: Function buttons
    "Power",
    "Fade",
    "Dimming",
    "Strobe",
    # Row 2: Mixed
    "Color1",
    "2-hour",
    "Color2",
    "4-hour",
    # Row 3: Modes + colors
    "Modes",
    "Deep_Red",
    "Mint",
    "Dark_Blue",
    # Row 4: Colors
    "Red_Prime",
    "Orange",
    "Light_Blue",
    "Violet",
    # Row 5: Colors
    "Green_Prime",
    "Golden_Rod",
    "Cyan",
    "Purple",
    # Row 6: Colors
    "Blue_Prime",
    "Yellow",
    "Steel_Blue",
    "Magenta",
    # Row 7: White
    "White_Select",
]


def capture_button(radio, button_name, num_presses=3):
    """Capture packets for a single button press.

    Args:
        radio: NRF24L01 instance
        button_name: Name of button being captured
        num_presses: Number of button presses to capture

    Returns:
        List of (channel, hex_string) tuples
    """
    all_packets = []

    for press_num in range(num_presses):
        print(f"      Press #{press_num+1}/3...")
        packets_before = len(all_packets)
        deadline = time.monotonic() + 0.5  # 500ms capture window per press

        while time.monotonic() < deadline:
            for ch in TARGET_CHANNELS:
                radio.set_channel(ch)
                if radio.available():
                    raw = radio.read()
                    hex_str = "".join(f"{b:02X}" for b in raw)
                    all_packets.append((ch, hex_str))

        packets_this_press = len(all_packets) - packets_before
        print(f"        -> captured {packets_this_press} packets")
        time.sleep(0.2)

    return all_packets


def main():
    print("=" * 70)
    print("  Jasco QOBRGBXYZA Remote — Systematic Button Capture")
    print(f"  Buttons: {len(BUTTONS)}")
    print(f"  Channels: {TARGET_CHANNELS} (2421, 2442, 2464 MHz)")
    print(f"  Data Rate: 1 Mbps")
    print("=" * 70)

    logfile = "rf_capture_log.txt"

    try:
        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        csn = digitalio.DigitalInOut(board.D8)
        ce = digitalio.DigitalInOut(board.D25)

        radio = NRF24L01(spi, csn, ce)
        radio.configure_promiscuous()

        print(f"\nCapture log: {logfile}\n")

        with open(logfile, 'w') as log:
            log.write("Jasco QOBRGBXYZA Remote RF Capture Log\n")
            log.write(f"Date: {time.ctime()}\n")
            log.write(f"Channels: {TARGET_CHANNELS}\n")
            log.write(f"Data Rate: 1 Mbps\n")
            log.write("=" * 70 + "\n\n")

            for btn_idx, button in enumerate(BUTTONS):
                print(f"  [{btn_idx+1:2}/{len(BUTTONS)}] {button}")
                input("    Press Enter when ready...")
                print("    Listening for 1.5 seconds (3 × 500ms presses)...")

                packets = capture_button(radio, button, num_presses=3)

                log.write(f"  {button} ({len(packets)} packets)\n")
                log.write("  " + "-" * 66 + "\n")

                # Log unique hex patterns (first 20 chars)
                unique_patterns = {}
                for ch, hex_str in packets:
                    key = hex_str[:20]
                    if key not in unique_patterns:
                        unique_patterns[key] = {'count': 0, 'channels': set()}
                    unique_patterns[key]['count'] += 1
                    unique_patterns[key]['channels'].add(ch)

                for hex_pattern in sorted(unique_patterns.keys(),
                                         key=lambda x: unique_patterns[x]['count'],
                                         reverse=True):
                    count = unique_patterns[hex_pattern]['count']
                    chans = sorted(unique_patterns[hex_pattern]['channels'])
                    log.write(f"    {hex_pattern}... ({count}x, ch: {chans})\n")

                log.write("\n")
                log.flush()
                print()

        print(f"\nCapture complete. Results saved to {logfile}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            radio.power_down()
            print("Radio powered down.")
        except Exception:
            pass


if __name__ == "__main__":
    main()
