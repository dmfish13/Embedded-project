#!/usr/bin/env python3
"""
Focused RF Capture on the three known remote channels.
Channels: 21 (2421 MHz), 42 (2442 MHz), 64 (2464 MHz)

Performs structured noise-vs-signal comparison:
  1. Records 10 seconds of ambient noise as baseline
  2. Walks user through 9 buttons, 10 seconds each
  3. Compares per-channel packet counts to find signal spikes
  4. Tests all three nRF24L01+ data rates automatically

Usage:
    python3 rf_scanner_reset.py
    python3 rf_capture.py
"""

import time
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

    def configure_promiscuous(self, data_rate="1mbps"):
        """Configure radio for promiscuous sniffing at the given data rate.

        Args:
            data_rate: "1mbps", "2mbps", or "250kbps"
        """
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
        if data_rate == "2mbps":
            self._write_reg(self.RF_SETUP, 0x0F)
        elif data_rate == "250kbps":
            self._write_reg(self.RF_SETUP, 0x27)
        else:
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

    def power_down(self):
        self._ce.value = False
        self._write_reg(self.CONFIG, 0x00)


TARGET_CHANNELS = [21, 42, 64]

BUTTONS = [
    "Power",
    "Fade",
    "Dimming",
    "Strobe",
    "Color1",
    "2-hour",
    "Color2",
    "4-hour",
    "Modes",
]


def scan_three_channels(radio, seconds):
    """Scan only channels 21, 42, 64 with 100ms dwell per channel.

    Returns:
        Tuple of (channel_counts_dict, list_of_(channel, hex) packets)
    """
    channels = {}
    packets = []
    deadline = time.monotonic() + seconds
    last_print = -1

    while time.monotonic() < deadline:
        remaining = int(deadline - time.monotonic())
        if remaining != last_print:
            last_print = remaining
            print(f"    {remaining}s remaining...")

        for ch in TARGET_CHANNELS:
            if time.monotonic() >= deadline:
                break
            radio.set_channel(ch)
            dwell_end = time.monotonic() + 0.1
            while time.monotonic() < dwell_end:
                if radio.available():
                    raw = radio.read()
                    hex_str = "".join(f"{b:02X}" for b in raw)
                    channels[ch] = channels.get(ch, 0) + 1
                    packets.append((ch, hex_str))

    return channels, packets


def run_test(radio, data_rate):
    """Run the full noise + 9 button test at a given data rate."""
    print(f"\n{'='*60}")
    print(f"  Testing at {data_rate}")
    print(f"{'='*60}")

    radio.configure_promiscuous(data_rate)

    # Noise baseline
    print("\n  [NOISE] Do NOT press any buttons.")
    input("  Press Enter to start 10-second noise capture...")
    print()
    noise_ch, noise_pkts = scan_three_channels(radio, 10)
    noise_total = sum(noise_ch.values())
    print(f"  Noise: {noise_total} packets")
    for ch in TARGET_CHANNELS:
        print(f"    ch {ch} ({2400+ch} MHz): {noise_ch.get(ch, 0)} packets")

    # Per-button captures
    all_results = {}
    for i, button in enumerate(BUTTONS):
        print(f"\n  [{i+1}/9] HOLD '{button}' button.")
        input(f"  Press Enter to start 10-second capture...")
        print()
        btn_ch, btn_pkts = scan_three_channels(radio, 10)

        spikes = {}
        for ch in TARGET_CHANNELS:
            n = noise_ch.get(ch, 0)
            b = btn_ch.get(ch, 0)
            if b > n:
                spikes[ch] = b - n

        all_results[button] = {
            "channels": btn_ch,
            "packets": btn_pkts,
            "spikes": spikes,
        }

        for ch in TARGET_CHANNELS:
            count = btn_ch.get(ch, 0)
            noise = noise_ch.get(ch, 0)
            diff = count - noise
            marker = " <<<" if diff > 2 else ""
            print(f"    ch {ch} ({2400+ch} MHz): {count} pkts "
                  f"(noise: {noise}, diff: {diff:+d}){marker}")

    # Summary table
    print(f"\n  {'='*50}")
    print(f"  SUMMARY for {data_rate}")
    print(f"  {'='*50}")
    print(f"\n  {'Button':<12}", end="")
    for ch in TARGET_CHANNELS:
        print(f"  ch{ch:>3}", end="")
    print()
    print(f"  {'-'*12}", end="")
    for _ in TARGET_CHANNELS:
        print(f"  {'-'*5}", end="")
    print()

    for button in BUTTONS:
        print(f"  {button:<12}", end="")
        for ch in TARGET_CHANNELS:
            diff = all_results[button]["spikes"].get(ch, 0)
            if diff > 0:
                print(f"  +{diff:>3}", end="")
            else:
                print(f"    {'.'}", end="")
        print()

    # Sample hex patterns
    for button in BUTTONS:
        pkts = all_results[button]["packets"]
        if pkts:
            unique = {}
            for ch, hex_str in pkts:
                key = hex_str[:20]
                if key not in unique:
                    unique[key] = ch
            if unique:
                print(f"\n  {button}:")
                for hex_val, ch in list(unique.items())[:5]:
                    print(f"    ch {ch}: {hex_val}")

    return all_results


def main():
    print("=" * 60)
    print("  Focused RF Capture")
    print("  Channels: 21 (2421MHz), 42 (2442MHz), 64 (2464MHz)")
    print("=" * 60)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)

    radio = NRF24L01(spi, csn, ce)

    # Test all three data rates
    for rate in ["1mbps", "2mbps", "250kbps"]:
        results = run_test(radio, rate)

        has_signal = False
        for button in BUTTONS:
            for ch, diff in results[button]["spikes"].items():
                if diff > 5:
                    has_signal = True
                    break

        if has_signal:
            print(f"\n  >>> SIGNAL DETECTED at {rate}! <<<")
            print("  You can skip remaining data rates.")
            answer = input("  Continue testing other rates? (y/n): ")
            if answer.lower() != "y":
                break

    try:
        radio.power_down()
    except Exception:
        pass

    print("\nDone.")


if __name__ == "__main__":
    main()
