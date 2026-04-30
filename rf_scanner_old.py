#!/usr/bin/env python3
import time
import board
import busio
import digitalio
from circuitpython_nrf24l01.rf24 import RF24

spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
csn = digitalio.DigitalInOut(board.D8)
ce = digitalio.DigitalInOut(board.D25)

nrf = RF24(spi, csn, ce)

def configure():
    nrf.crc = 0
    nrf.auto_ack = False
    nrf.address_length = 2
    nrf.open_rx_pipe(0, b"\x00\x55")
    nrf.open_rx_pipe(1, b"\x00\xAA")
    nrf.payload_length = 32
    nrf.data_rate = 1
    nrf.listen = True

def main():
    configure()
    print("--- Sniffer Active: Sweeping 2.4GHz Band ---")
    print("Hold a remote button and look for 'HIT' messages.")

    try:
        while True:
            for ch in range(126):
                nrf.listen = False
                nrf.channel = ch
                nrf.listen = True
                if nrf.available():
                    raw_payload = nrf.read()
                    hex_str = "".join(f"{b:02X}" for b in raw_payload)[:10]
                    print(f"HIT! Channel: {ch} | Hex ID: {hex_str}")
                time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nSniffing stopped by user.")

if __name__ == "__main__":
    main()
