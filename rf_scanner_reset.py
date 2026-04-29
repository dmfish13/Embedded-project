#!/usr/bin/env python3
"""
nRF24L01+ Radio State Recovery Tool

Resets the radio to factory defaults by writing 0x08 to the CONFIG
register and flushing both TX and RX FIFO buffers. Use this script
after a crash or interrupted scan leaves the radio in a bad state.

Usage:
    python3 rf_scanner_reset.py
"""

import time
import board
import busio
import digitalio

spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
csn = digitalio.DigitalInOut(board.D8)
csn.direction = digitalio.Direction.OUTPUT
ce = digitalio.DigitalInOut(board.D25)
ce.direction = digitalio.Direction.OUTPUT

# Power-cycle the radio via register reset
ce.value = False
csn.value = False
time.sleep(0.01)
csn.value = True
time.sleep(0.01)

# Write 0x08 to CONFIG register (reset default)
while not spi.try_lock():
    pass
spi.configure(baudrate=1000000)
csn.value = False
spi.write(bytearray([0x20, 0x08]))  # 0x20 = write CONFIG
csn.value = True

# Flush TX and RX FIFOs
csn.value = False
spi.write(bytearray([0xE1]))  # FLUSH_TX
csn.value = True
csn.value = False
spi.write(bytearray([0xE2]))  # FLUSH_RX
csn.value = True

spi.unlock()
print("Radio reset to defaults.")
