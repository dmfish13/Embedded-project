#!/usr/bin/env python3
"""
Reset-then-send test — holds DIN HIGH for an extended period to force
all TM1815B chips in the chain into reset, then immediately sends
data before demo mode can re-activate.

Also tests parallel-friendly mode (NUM_LEDS=1) for star topology.

Usage:
    python3 led_reset_test.py
"""

import time
import sys
import subprocess
import threading
from spidev import SpiDev

NUM_LEDS = 4
SPI_SPEED = 2_000_000


def encode_byte(value):
    """Logic 1 -> 0b0001 (long LOW), Logic 0 -> 0b0111 (short LOW)."""
    encoded = 0
    for bit_pos in range(7, -1, -1):
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001
        else:
            encoded = (encoded << 4) | 0b0111
    return bytes([
        (encoded >> 24) & 0xFF, (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF, encoded & 0xFF,
    ])


LUT = [encode_byte(v) for v in range(256)]


def build_frame(r, g, b, w, num_leds, current=30,
                preamble_bytes=80, reset_bytes=100):
    c1_val = current & 0x3F
    c1 = bytes([c1_val] * 4)
    c2 = bytes([c1_val ^ 0xFF] * 4)

    buf = bytearray(b'\xFF' * preamble_bytes)
    for bv in c1:
        buf += LUT[bv]
    for bv in c2:
        buf += LUT[bv]
    for _ in range(num_leds):
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
    buf += b'\xFF' * reset_bytes
    return buf


def gpio_high(pin=20):
    """Set GPIO 20 to output HIGH (hold DIN in reset state)."""
    subprocess.run(["pinctrl", "set", str(pin), "op", "dh"],
                   capture_output=True)


def gpio_to_spi(pin=20):
    """Return GPIO 20 to SPI1 MOSI alt function."""
    subprocess.run(["pinctrl", "set", str(pin), "a5"],
                   capture_output=True)


def hold_reset(seconds):
    """Hold DIN HIGH via GPIO for a specified duration."""
    gpio_high(20)
    print(f"    Holding DIN HIGH (reset) for {seconds} seconds...")
    time.sleep(seconds)


def send_continuous(r, g, b, w, num_leds, label=""):
    """Send frames continuously until Enter is pressed."""
    gpio_to_spi(20)

    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    spi.lsbfirst = False

    buf = list(build_frame(r, g, b, w, num_leds, current=30))
    frame_count = 0
    running = True

    if label:
        print(f"    {label}")
    print("    Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait_for_enter():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait_for_enter, daemon=True)
    t.start()

    while running:
        spi.xfer2(buf)
        frame_count += 1

    spi.close()
    gpio_high(20)
    return frame_count


def main():
    print("=" * 62)
    print("  Reset-Then-Send LED Test")
    print("  Tests long reset periods before sending data")
    print("=" * 62)

    tests = [
        {
            "name": "1-second reset, then RED to 4 LEDs",
            "reset_s": 1.0,
            "r": 255, "g": 0, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then RED to 4 LEDs",
            "reset_s": 5.0,
            "r": 255, "g": 0, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then GREEN to 4 LEDs",
            "reset_s": 5.0,
            "r": 0, "g": 255, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then ALL OFF to 4 LEDs",
            "reset_s": 5.0,
            "r": 0, "g": 0, "b": 0, "w": 0,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then WHITE (W channel) to 4 LEDs",
            "reset_s": 5.0,
            "r": 0, "g": 0, "b": 0, "w": 255,
            "num_leds": 4,
        },
        {
            "name": "5-second reset, then RED to 8 LEDs (extra data)",
            "reset_s": 5.0,
            "r": 255, "g": 0, "b": 0, "w": 0,
            "num_leds": 8,
        },
    ]

    # Start with GPIO HIGH
    gpio_high(20)
    time.sleep(0.5)

    for i, test in enumerate(tests, 1):
        print(f"\n  [{i}/{len(tests)}] {test['name']}")
        input("    Press Enter to start...")

        hold_reset(test["reset_s"])

        frames = send_continuous(
            test["r"], test["g"], test["b"], test["w"],
            test["num_leds"],
            label=f"RGBW=({test['r']},{test['g']},{test['b']},{test['w']}) "
                  f"x{test['num_leds']} LEDs"
        )
        print(f"    Sent {frames} frames")
        time.sleep(2)

    print("\n  Done.")
    print("  Key observations:")
    print("    - Did the long reset stop the cycling before data was sent?")
    print("    - Did any test light up ALL 4 series LEDs?")
    print("    - Did the 'ALL OFF' test turn any LEDs off?")


def build_protocol_frame(pixels, current=30, spi_speed=1_600_000):
    """Build a TM1815B frame that strictly follows the datasheet protocol.

    Frame structure (all data MSB first):
        RESET:  HIGH >= 200 µs
        C1:     W[7:0] R[7:0] G[7:0] B[7:0]  (bits 7,6 = 0, bits 5:0 = current)
        C2:     bitwise inverse of C1
        D1:     W[7:0] R[7:0] G[7:0] B[7:0]  (PWM data for chip 1)
        D2:     W[7:0] R[7:0] G[7:0] B[7:0]  (PWM data for chip 2)
        ...
        Dn:     W[7:0] R[7:0] G[7:0] B[7:0]  (PWM data for chip n)
        RESET:  HIGH >= 200 µs  (latch — chips validate C1/C2 and apply PWM)

    Forwarding (automatic, 32-bit delay per chip):
        Chip 1 receives C1, stores it. DO stays HIGH.
        Chip 1 receives C2, stores it. DO starts forwarding C1.
        Chip 1 receives D1, stores it (own data). DO forwards C2.
        Chip 1 receives D2..Dn, forwards each on DO.
        On RESET: Chip 1 validates C2==~C1, applies current + D1 PWM.

    The large preamble/reset padding ensures the Pi 5 SPI controller
    uses DMA for a gap-free transfer, critical at 1.6 MHz where the
    timing is exactly at spec (625 ns/SPI-bit = 400 kHz data rate).
    """
    # Current setting — 6-bit value per channel, bits 7,6 must be 0
    c1_w = current & 0x3F
    c1_r = current & 0x3F
    c1_g = current & 0x3F
    c1_b = current & 0x3F
    c1 = bytes([c1_w, c1_r, c1_g, c1_b])
    c2 = bytes([b ^ 0xFF for b in c1])

    # Large reset padding to force DMA and exceed 200 µs minimum
    # At 1.6 MHz: 2000 bytes = 10 ms HIGH (50x the 200 µs minimum)
    # At 2.0 MHz: 2000 bytes = 8 ms HIGH (40x the 200 µs minimum)
    preamble = b'\xFF' * 2000

    # Encode C1, C2, and pixel data
    data = bytearray()
    for bv in c1:
        data += LUT[bv]
    for bv in c2:
        data += LUT[bv]
    for w, r, g, b in pixels:
        data += LUT[w] + LUT[r] + LUT[g] + LUT[b]

    # Trailing reset — latch period
    trailing = b'\xFF' * 2000

    return bytearray(preamble) + data + bytearray(trailing)


def protocol_test():
    """Test strict TM1815B protocol with proper forwarding.

    Sends a single complete frame via SPI with GPIO-controlled reset,
    then holds the line HIGH so the latch reset is clean. Tests at
    both 1.6 MHz (in-spec 400 kHz data rate) and 2.0 MHz.
    """
    print("\n" + "=" * 62)
    print("  TM1815B Protocol-Compliant Forwarding Test")
    print("  Follows datasheet: Reset → C1 → C2 → D1..D4 → Reset")
    print("  Large buffer forces DMA for gap-free SPI transfer")
    print("=" * 62)

    # 4 pixels: each a (W, R, G, B) tuple — different color per LED
    # so we can see which LEDs in the chain receive their correct data
    pixel_sets = [
        {
            "name": "Each LED a different color (R, G, B, W)",
            "pixels": [
                (0,   255, 0,   0),    # D1: LED1 = Red
                (0,   0,   255, 0),    # D2: LED2 = Green
                (0,   0,   0,   255),  # D3: LED3 = Blue
                (255, 0,   0,   0),    # D4: LED4 = White
            ],
        },
        {
            "name": "All LEDs RED",
            "pixels": [
                (0, 255, 0, 0),
                (0, 255, 0, 0),
                (0, 255, 0, 0),
                (0, 255, 0, 0),
            ],
        },
        {
            "name": "All LEDs OFF (all PWM = 0)",
            "pixels": [
                (0, 0, 0, 0),
                (0, 0, 0, 0),
                (0, 0, 0, 0),
                (0, 0, 0, 0),
            ],
        },
    ]

    speeds = [
        (1_600_000, "1.6 MHz (400 kHz data rate — in spec)"),
        (2_000_000, "2.0 MHz (500 kHz data rate — above spec, known to work for LED1)"),
    ]

    gpio_high(20)
    time.sleep(0.5)

    test_num = 0
    for speed_hz, speed_desc in speeds:
        for pset in pixel_sets:
            test_num += 1
            print(f"\n  [{test_num}] {speed_desc}")
            print(f"      {pset['name']}")
            for i, (w, r, g, b) in enumerate(pset["pixels"], 1):
                print(f"      D{i} (LED{i}): W={w:>3} R={r:>3} G={g:>3} B={b:>3}")

            input("      Press Enter to start...")

            # Phase 1: Hold DIN HIGH via GPIO for clean reset
            hold_reset(3.0)

            # Phase 2: Build and send one protocol-compliant frame
            frame = build_protocol_frame(pset["pixels"], current=30,
                                         spi_speed=speed_hz)
            print(f"      Frame: {len(frame)} bytes "
                  f"({len(frame) * 8 / speed_hz * 1000:.1f} ms)")

            gpio_to_spi(20)
            spi = SpiDev()
            spi.open(1, 0)
            spi.max_speed_hz = speed_hz
            spi.mode = 0b00
            spi.lsbfirst = False

            # Send the frame once — the large buffer ensures DMA
            spi.xfer2(list(frame))

            spi.close()

            # Phase 3: Immediately hold DIN HIGH via GPIO for latch
            gpio_high(20)
            print("      Sent 1 frame. Holding DIN HIGH (latch reset).")
            print("      Observe LEDs for 5 seconds...")
            time.sleep(5)

            # Phase 4: Now send continuously to maintain the state
            print("      Now sending continuously. Press Enter to stop.")
            sys.stdout.flush()
            frames = send_continuous(
                pset["pixels"][0][1],  # R from first pixel
                pset["pixels"][0][2],  # G
                pset["pixels"][0][3],  # B
                pset["pixels"][0][0],  # W
                len(pset["pixels"]),
            )
            print(f"      Sent {frames} continuous frames")
            time.sleep(2)

    print("\n  Done.")
    print("  Key questions:")
    print("    - Did single-frame sends light up any LEDs?")
    print("    - Did 1.6 MHz (large buffer DMA) work for ALL 4 LEDs?")
    print("    - Did each LED show its own color in test 1?")
    print("      (LED1=Red, LED2=Green, LED3=Blue, LED4=White)")
    print("    - Did continuous sending behave differently from single?")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "protocol":
        protocol_test()
    else:
        main()


if __name__ == "__main__":
    main()
