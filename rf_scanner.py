#!/usr/bin/env python3
"""
RF Scanner for the Jasco QOBRGBXYZA remote using an nRF24L01+ transceiver.

This script sweeps all 126 nRF24 channels (2.400 – 2.525 GHz) in promiscuous
mode to capture raw RF packets from the Enbrighten remote.  It is designed to
run on a Raspberry Pi 5 with the radio wired to SPI0.

Hardware wiring (nRF24L01+ → Raspberry Pi 5):
    VCC  → Pin 17  (3.3 V)
    GND  → Pin 20  (GND)
    CE   → GPIO 25
    CSN  → GPIO 8  (SPI0 CE0)
    SCK  → GPIO 11 (SPI0 SCLK)
    MOSI → GPIO 10 (SPI0 MOSI)
    MISO → GPIO 9  (SPI0 MISO)

Key configuration choices
-------------------------
* CRC is **disabled** so the radio does not silently discard non-nRF24 packets.
* Auto-acknowledge is **disabled** (no ACK expected from the remote).
* Address width is set to the **minimum 2 bytes** to maximise the chance of
  matching a random preamble from the Jasco remote.
* The data rate is set to **1 Mbps** (a common default for consumer 2.4 GHz
  devices).  If nothing is captured, try switching to 2 Mbps.
* Payload size is fixed at **32 bytes** (the nRF24 maximum) so we grab as
  much raw data as possible per reception.

Usage:
    python3 rf_scanner.py                 # sweep all 126 channels
    python3 rf_scanner.py --channel 76    # listen on a single channel
    python3 rf_scanner.py --start 60 --end 80   # sweep a sub-range
"""

import time
import argparse
import struct

import board
import digitalio
from circuitpython_nrf24l01.rf24 import RF24


# ---------------------------------------------------------------------------
# Pin setup — SPI0 on the Raspberry Pi 5
# ---------------------------------------------------------------------------
# CE  = GPIO 25
# CSN = GPIO 8 (active-low chip-select, directly mapped to SPI0 CE0)
ce_pin = digitalio.DigitalInOut(board.D25)
csn_pin = digitalio.DigitalInOut(board.D8)

# SPI bus (SPI0 — SCLK=GPIO11, MOSI=GPIO10, MISO=GPIO9)
spi = board.SPI()

# Instantiate the radio
nrf = RF24(spi, csn_pin, ce_pin)


# ---------------------------------------------------------------------------
# Radio configuration — promiscuous / raw capture mode
# ---------------------------------------------------------------------------
def configure_radio(channel=None):
    """Set up the nRF24L01+ for raw, promiscuous packet sniffing.

    Args:
        channel: Optional fixed channel (0-125).  If None the caller will
                 sweep channels manually.
    """
    # Disable CRC — critical for seeing non-nRF24 traffic
    nrf.crc = 0  # 0 = disabled, 1 = 1-byte CRC, 2 = 2-byte CRC

    # Disable auto-acknowledge on all pipes
    nrf.auto_ack = False

    # Minimum address width (2 bytes) to catch more arbitrary preambles
    nrf.address_length = 2

    # Use a very generic 2-byte address: 0x00 0x55
    # 0x55 = 0b01010101 — alternating bits that commonly appear after
    # the nRF24 preamble byte, increasing capture probability.
    rx_address = b"\x00\x55"
    nrf.open_rx_pipe(0, rx_address)

    # Also try the inverted pattern on pipe 1
    rx_address_alt = b"\x00\xAA"
    nrf.open_rx_pipe(1, rx_address_alt)

    # Fixed 32-byte payload (maximum) — grab as much data as we can
    nrf.payload_length = 32

    # 1 Mbps is a good starting point for unknown consumer remotes
    nrf.data_rate = 1  # 1 = 1 Mbps, 2 = 2 Mbps, 250 = 250 kbps

    # Maximum PA level for best receive sensitivity
    nrf.pa_level = -12  # dBm (-12 is a safe starting point)

    # Set channel if a fixed one was requested
    if channel is not None:
        nrf.channel = channel

    # Put the radio into RX mode
    nrf.listen = True


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------
def scan_channel(ch, dwell_ms=50):
    """Listen on a single channel for *dwell_ms* and return any captured data.

    Args:
        ch:       nRF24 channel number (0-125).
        dwell_ms: How long to listen on this channel in milliseconds.

    Returns:
        List of (channel, raw_bytes) tuples captured during the dwell.
    """
    nrf.listen = False
    nrf.channel = ch
    nrf.listen = True

    captured = []
    deadline = time.monotonic() + (dwell_ms / 1000.0)

    while time.monotonic() < deadline:
        if nrf.available():
            raw = nrf.read()
            captured.append((ch, raw))

    return captured


def sweep(start_ch=0, end_ch=125, dwell_ms=50):
    """Sweep a range of channels once, returning everything captured.

    Args:
        start_ch: First channel to scan (inclusive).
        end_ch:   Last channel to scan (inclusive).
        dwell_ms: Time in ms to dwell on each channel.

    Returns:
        List of (channel, raw_bytes) tuples.
    """
    results = []
    for ch in range(start_ch, end_ch + 1):
        results.extend(scan_channel(ch, dwell_ms))
    return results


def format_payload(raw):
    """Return a hex-formatted string of a raw payload."""
    return " ".join(f"{b:02X}" for b in raw)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
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
        "--dwell", type=int, default=50,
        help="Dwell time per channel in ms (default: 50)."
    )
    parser.add_argument(
        "--rounds", type=int, default=0,
        help="Number of sweep rounds (0 = infinite, default: 0)."
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  nRF24L01+ RF Scanner — Jasco QOBRGBXYZA Remote")
    print("=" * 70)

    configure_radio(channel=args.channel)

    if args.channel is not None:
        print(f"  Mode       : Fixed channel {args.channel}")
        freq_mhz = 2400 + args.channel
        print(f"  Frequency  : {freq_mhz} MHz")
    else:
        print(f"  Mode       : Sweep channels {args.start}–{args.end}")
        print(f"  Freq range : {2400 + args.start}–{2400 + args.end} MHz")

    print(f"  Dwell      : {args.dwell} ms per channel")
    print(f"  CRC        : disabled")
    print(f"  Auto-ACK   : disabled")
    print(f"  Addr width : 2 bytes")
    print(f"  Payload    : 32 bytes (fixed)")
    print(f"  Data rate  : 1 Mbps")
    print("=" * 70)
    print("\nPress Ctrl+C to stop.\n")

    packet_count = 0
    round_num = 0

    try:
        while True:
            round_num += 1
            if args.rounds > 0 and round_num > args.rounds:
                break

            if args.channel is not None:
                # Fixed-channel mode: just keep reading
                hits = scan_channel(args.channel, dwell_ms=args.dwell)
            else:
                # Sweep mode
                print(f"--- Sweep round {round_num} "
                      f"(channels {args.start}–{args.end}) ---")
                hits = sweep(args.start, args.end, dwell_ms=args.dwell)

            for ch, raw in hits:
                packet_count += 1
                freq_mhz = 2400 + ch
                ts = time.strftime("%H:%M:%S")
                hex_str = format_payload(raw)
                print(f"[{ts}]  #{packet_count:<6}  ch={ch:>3}  "
                      f"({freq_mhz} MHz)  {hex_str}")

            if not hits and args.channel is None:
                print("  (no packets this round)")

    except KeyboardInterrupt:
        print(f"\n\nStopped. Total packets captured: {packet_count}")
    finally:
        nrf.listen = False
        nrf.power = False
        print("Radio powered down.")


if __name__ == "__main__":
    main()
rf_scanner
