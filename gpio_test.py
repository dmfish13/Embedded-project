#!/usr/bin/env python3
"""Minimal GPIO test: send all-white to 12 TM1815B LEDs via bit-bang."""
import gpiod
import time
from gpiod.line import Direction, Value

CHIP = "/dev/gpiochip4"
PIN = 20
NUM_LEDS = 12

request = gpiod.request_lines(
    CHIP,
    consumer="led_test",
    config={PIN: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)},
)

def set_pin(val):
    request.set_value(PIN, Value.ACTIVE if val else Value.INACTIVE)

def send_bit(val):
    if val:
        set_pin(1); set_pin(1)
        set_pin(0)
    else:
        set_pin(1)
        set_pin(0); set_pin(0)

def send_byte(b):
    for i in range(7, -1, -1):
        send_bit(b & (1 << i))

def send_pixel(g, r, b, w):
    send_byte(g); send_byte(r); send_byte(b); send_byte(w)

# Reset
set_pin(0)
time.sleep(0.001)

# Send all white
for _ in range(NUM_LEDS):
    send_pixel(0, 0, 0, 255)

# Reset to latch
set_pin(0)
time.sleep(0.001)

print("Sent all-white to 12 LEDs on GPIO 20. Are the LEDs white?")
request.release()
