#!/usr/bin/env python3
"""
nRF24L01+ Hardware Diagnostic Tool

Performs four sequential tests to verify SPI communication between
the Raspberry Pi 5 and the nRF24L01+ transceiver:
  1. SPI bus acquisition
  2. GPIO 8 (CSN) pin claim
  3. GPIO 25 (CE) pin claim
  4. Raw SPI read of CONFIG register (expect 0x08)

Hardware wiring (nRF24L01+ -> Raspberry Pi 5):
    VCC  -> Pin 17  (3.3 V)
    GND  -> Pin 20  (GND)
    CE   -> GPIO 25 (Pin 22)
    CSN  -> GPIO 8  (Pin 24)
    SCK  -> GPIO 11 (Pin 23)
    MOSI -> GPIO 10 (Pin 19)
    MISO -> GPIO 9  (Pin 21)

Usage:
    python3 rf_scanner_debugger.py
"""

import busio
import board
import digitalio

print("=== SPI + nRF24L01+ Diagnostic ===\n")

# Test 1: Can we get the SPI bus?
print("[1] SPI bus...")
try:
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    print("    OK\n")
except Exception as e:
    print(f"    FAIL: {e}\n")
    exit()

# Test 2: Can we claim GPIO 8 (CSN)?
print("[2] GPIO 8 (CSN)...")
try:
    csn = digitalio.DigitalInOut(board.D8)
    csn.direction = digitalio.Direction.OUTPUT
    csn.value = True
    print("    OK\n")
except Exception as e:
    print(f"    FAIL: {e}\n")
    exit()

# Test 3: Can we claim GPIO 25 (CE)?
print("[3] GPIO 25 (CE)...")
try:
    ce = digitalio.DigitalInOut(board.D25)
    ce.direction = digitalio.Direction.OUTPUT
    ce.value = False
    print("    OK\n")
except Exception as e:
    print(f"    FAIL: {e}\n")
    exit()

# Test 4: Raw SPI read of the CONFIG register
print("[4] Raw SPI read (CONFIG register)...")
try:
    while not spi.try_lock():
        pass
    spi.configure(baudrate=1000000)

    csn.value = False
    buf = bytearray([0x00, 0xFF])
    spi.write_readinto(buf, buf)
    csn.value = True
    spi.unlock()

    val = buf[1]
    print(f"    Response: 0x{val:02X}")
    if val == 0x00 or val == 0xFF:
        print("    FAIL: No response from radio.")
        print("    --> Check wiring: SCK, MOSI, MISO, CSN, VCC, GND")
        print("    --> Try a 10uF capacitor across VCC and GND")
    elif val == 0x08:
        print("    OK: Radio is responding (default CONFIG = 0x08)")
    else:
        print(f"    Radio responded with non-default value (may still be OK)")
except Exception as e:
    print(f"    FAIL: {e}")

print("\n=== Done ===")
