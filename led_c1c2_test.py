#!/usr/bin/env python3
"""
C1/C2 forwarding test — finds the SPI speed and C1/C2 values that
enable downstream TM1815B chips to receive forwarded data.

Per the TM1815B datasheet:
  - Frame: [Reset][C1][C2][D1][D2]...[Dn][Reset]
  - C1 (32 bits): constant-current setting, bits 7,6 of each byte = 0
  - C2 (32 bits): must be bitwise NOT of C1
  - D(n) (32 bits): PWM data for LED PCB n (W, R, G, B — 8 bits each)
  - After receiving C1, chip forwards C1 on DO while receiving C2
  - Each chip keeps its first D packet, forwards the rest
  - Logic 0 LOW time: 620-820 ns (2.0 MHz gives 500ns = OUT OF SPEC)
  - Logic 1 LOW time: 1300-2000 ns
  - Reset: HIGH >= 200 us

Setup: Pi → SN74AHCT125N → PCB1 → PCB2 → PCB3 → PCB4
       (10kΩ pull-up on GPIO 20 to 3.3V)

Usage:
    python3 led_c1c2_test.py
"""

import sys
import threading
from spidev import SpiDev

NUM_LEDS = 4


def encode_byte(value):
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


def build_frame(c1_bytes, c2_bytes, pixels, reset_bytes=80):
    """Build a TM1815B frame: [Reset][C1][C2][D1..Dn][Reset]

    c1_bytes: list of 4 bytes [W, R, G, B] (bits 7,6 must be 0)
    c2_bytes: list of 4 bytes [W, R, G, B] (must be ~C1)
    pixels:   list of (W, R, G, B) tuples, one per LED PCB
    reset_bytes: number of 0xFF bytes for reset period
    """
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += LUT[bv]
    for bv in c2_bytes:
        buf += LUT[bv]
    for w, r, g, b in pixels:
        buf += LUT[w] + LUT[r] + LUT[g] + LUT[b]
    buf += b'\xFF' * reset_bytes
    return buf


def run_test(buf_list, spi_speed):
    """Send continuously at given speed until Enter is pressed."""
    spi = SpiDev()
    spi.open(1, 0)
    spi.max_speed_hz = spi_speed
    spi.mode = 0b00
    spi.lsbfirst = False

    frame_count = 0
    running = True

    print("         Sending... Press Enter to stop.")
    sys.stdout.flush()

    def wait():
        nonlocal running
        input()
        running = False

    t = threading.Thread(target=wait, daemon=True)
    t.start()

    while running:
        spi.xfer2(buf_list)
        frame_count += 1

    spi.close()
    return frame_count


def fmt_c(c_bytes):
    return f"[0x{c_bytes[0]:02X}, 0x{c_bytes[1]:02X}, 0x{c_bytes[2]:02X}, 0x{c_bytes[3]:02X}]"


def fmt_d(pixel):
    return f"W={pixel[0]:>3} R={pixel[1]:>3} G={pixel[2]:>3} B={pixel[3]:>3}"


def print_test_info(c1, c2, pixels):
    """Print C1, C2, and each D(n) on separate lines."""
    for i, px in enumerate(pixels, 1):
        print(f"         C1={fmt_c(c1)}  C2={fmt_c(c2)}  "
              f"D{i}: {fmt_d(px)}")


TESTS = [
    # =================================================================
    # SECTION 1: SPI speed comparison — same frame, different speeds
    # At 1.6 MHz: logic 0 LOW = 625ns (in spec: 620-820ns)
    # At 2.0 MHz: logic 0 LOW = 500ns (OUT OF SPEC: < 620ns min)
    # If forwarding works at 1.6 but not 2.0, timing is the issue.
    # =================================================================
    {
        "section": "\n  === SECTION 1: Speed comparison (unique colors per PCB) ===",
        "name": "1.6 MHz — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "2.0 MHz — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 2_000_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "1.8 MHz — PCB1=RED, PCB2=GREEN, PCB3=BLUE, PCB4=WHITE",
        "speed": 1_800_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 2: C1 current values at 1.6 MHz
    # Tests if a specific current value is needed for forwarding.
    # All use unique colors to verify each PCB gets its own data.
    # =================================================================
    {
        "section": "\n  === SECTION 2: C1 current values @ 1.6 MHz ===",
        "name": "Current=0 (minimum 6.5mA) — unique colors",
        "speed": 1_600_000,
        "c1": [0x00, 0x00, 0x00, 0x00],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "Current=30 (0x1E, ~19mA) — unique colors",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "Current=63 (0x3F, maximum 38mA) — unique colors",
        "speed": 1_600_000,
        "c1": [0x3F, 0x3F, 0x3F, 0x3F],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 3: Reset length at 1.6 MHz
    # Datasheet says reset >= 200us. At 1.6 MHz, 80 bytes = 400us.
    # Also: inter-byte HIGH must NOT exceed 126us or chip resets.
    # =================================================================
    {
        "section": "\n  === SECTION 3: Reset length @ 1.6 MHz, current=30 ===",
        "name": "Reset=40 bytes (200us) — minimum per datasheet",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
        "reset": 40,
    },
    {
        "name": "Reset=80 bytes (400us) — standard",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
        "reset": 80,
    },
    {
        "name": "Reset=200 bytes (1ms)",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
        "reset": 200,
    },

    # =================================================================
    # SECTION 4: All same color at 1.6 MHz
    # If all PCBs show the same color, forwarding works but we can't
    # distinguish addressing. Use as a simpler forwarding check.
    # =================================================================
    {
        "section": "\n  === SECTION 4: All same color @ 1.6 MHz, current=30 ===",
        "name": "All RED",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0)] * NUM_LEDS,
    },
    {
        "name": "All GREEN",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 255, 0)] * NUM_LEDS,
    },
    {
        "name": "All BLUE",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 255)] * NUM_LEDS,
    },

    # =================================================================
    # SECTION 5: Individual PCB addressing at 1.6 MHz
    # Only one PCB lit at a time — confirms forwarding reaches each.
    # =================================================================
    {
        "section": "\n  === SECTION 5: One PCB at a time @ 1.6 MHz, current=30 ===",
        "name": "Only PCB1 = RED",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB2 = GREEN",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 0), (0, 0, 255, 0), (0, 0, 0, 0), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB3 = BLUE",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 255), (0, 0, 0, 0)],
    },
    {
        "name": "Only PCB4 = WHITE",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 6: Fine speed sweep around the spec boundary
    # Logic 0 LOW must be 620-820ns. Find the fastest working speed.
    # =================================================================
    {
        "section": "\n  === SECTION 6: Fine speed sweep (unique colors) ===",
        "name": "1.4 MHz (logic 0 LOW = 714ns)",
        "speed": 1_400_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "1.6 MHz (logic 0 LOW = 625ns)",
        "speed": 1_600_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },
    {
        "name": "1.7 MHz (logic 0 LOW = 588ns — below 620ns spec)",
        "speed": 1_700_000,
        "c1": [0x1E, 0x1E, 0x1E, 0x1E],
        "pixels": [(0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255), (255, 0, 0, 0)],
    },

    # =================================================================
    # SECTION 7: All off (baseline)
    # =================================================================
    {
        "section": "\n  === SECTION 7: All off ===",
        "name": "All off @ 1.6 MHz",
        "speed": 1_600_000,
        "c1": [0x00, 0x00, 0x00, 0x00],
        "pixels": [(0, 0, 0, 0)] * NUM_LEDS,
    },
]


def main():
    print("=" * 66)
    print("  TM1815B C1/C2 Forwarding Test")
    print(f"  {NUM_LEDS} PCBs daisy-chained: PCB1 → PCB2 → PCB3 → PCB4")
    print()
    print("  Datasheet timing requirements:")
    print("    Logic 0 LOW: 620-820 ns    Logic 1 LOW: 1300-2000 ns")
    print("    At 1.6 MHz SPI: 0=625ns, 1=1875ns  (BOTH IN SPEC)")
    print("    At 2.0 MHz SPI: 0=500ns, 1=1500ns  (0 OUT OF SPEC)")
    print()
    print("  Frame: [Reset 0xFF] [C1] [C2] [D1] [D2] [D3] [D4] [Reset 0xFF]")
    print("  C2 = bitwise NOT of C1")
    print()
    print("  Press Enter to START each test, Enter again to STOP.")
    print("=" * 66)

    for i, test in enumerate(TESTS, 1):
        if "section" in test:
            print(test["section"])

        c1 = test["c1"]
        c2 = [b ^ 0xFF for b in c1]
        pixels = test["pixels"]
        speed = test["speed"]
        reset = test.get("reset", 80)
        t0_ns = int(1e9 / speed)

        print(f"\n  [{i}/{len(TESTS)}] {test['name']}")
        print(f"         SPI: {speed/1e6:.1f} MHz | "
              f"Logic 0 LOW: {t0_ns}ns | "
              f"Reset: {reset} bytes")
        print_test_info(c1, c2, pixels)

        input("         Press Enter to start...")

        buf = build_frame(c1, c2, pixels, reset_bytes=reset)
        frames = run_test(list(buf), speed)
        print(f"         Sent {frames} frames")

    print("\n  Done. Key questions:")
    print("    1. Did any speed make PCBs 2-4 respond? (Section 1)")
    print("    2. Did current value matter for forwarding? (Section 2)")
    print("    3. Did reset length affect behavior? (Section 3)")
    print("    4. At the working speed, did each PCB show its own color? (Sec 4-5)")
    print("    5. What is the fastest speed that still forwards? (Section 6)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
