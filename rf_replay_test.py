#!/usr/bin/env python3
"""
RF Replay Transmitter — Test if the LED controller accepts replayed packets.

Captures a raw command packet, then retransmits it to see if the LED
controller responds. This determines whether the protocol enforces
rolling code validation (unlikely for cheap LED controllers).

The nRF24L01+ transmits the raw payload bytes on the scrambled address.
The XN297-based receiver in the LED controller will descramble them
identically to the original remote's transmission.

Usage:
    python3 rf_scanner_reset.py
    python3 rf_replay_test.py
"""

import time
import board
import busio
import digitalio


BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])

SCRAMBLE_B = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
]

NRF24_ADDR = [0x38, 0x72, 0x2D, 0xA8, 0x5E]
ADDR_WIDTH = 5
CHANNEL = 42

COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])


class NRF24L01:
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
        tx = bytearray([0x61] + [0xFF] * length)
        rx = bytearray(len(tx))
        self._spi.write_readinto(tx, rx)
        self._csn.value = True
        self._spi.unlock()
        return rx[1:]

    def _write_payload(self, data):
        while not self._spi.try_lock():
            pass
        self._spi.configure(baudrate=1000000)
        self._csn.value = False
        self._spi.write(bytearray([0xA0]) + bytearray(data))
        self._csn.value = True
        self._spi.unlock()

    def configure_rx(self):
        self._ce.value = False
        self._write_reg(0x00, 0x03)  # PWR_UP, PRIM_RX, no CRC
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)
        self._write_reg(0x02, 0x01)
        self._write_reg(0x03, ADDR_WIDTH - 2)
        self._write_reg_bytes(0x0A, bytes(NRF24_ADDR))
        self._write_reg(0x11, 32)
        self._write_reg(0x06, 0x07)  # 1 Mbps
        self._write_reg(0x1C, 0x00)
        self._write_reg(0x1D, 0x00)
        self._command(0xE2)
        self._command(0xE1)
        self._write_reg(0x07, 0x70)
        self._write_reg(0x05, CHANNEL)
        self._ce.value = True

    def configure_tx(self):
        self._ce.value = False
        self._write_reg(0x00, 0x02)  # PWR_UP, PTX, no CRC
        time.sleep(0.002)
        self._write_reg(0x01, 0x00)  # no auto-ack
        self._write_reg(0x02, 0x00)  # no RX pipes needed
        self._write_reg(0x03, ADDR_WIDTH - 2)
        self._write_reg_bytes(0x10, bytes(NRF24_ADDR))  # TX address
        self._write_reg(0x04, 0x00)  # no retransmit
        self._write_reg(0x06, 0x07)  # 1 Mbps
        self._write_reg(0x1C, 0x00)
        self._write_reg(0x1D, 0x00)
        self._command(0xE2)
        self._command(0xE1)
        self._write_reg(0x07, 0x70)
        self._write_reg(0x05, CHANNEL)

    def transmit(self, payload):
        self._command(0xE1)  # flush TX
        self._write_reg(0x07, 0x70)  # clear flags
        self._write_payload(payload)
        self._ce.value = True
        time.sleep(0.001)  # 10us minimum, use 1ms for safety
        self._ce.value = False
        # Wait for TX complete or timeout
        deadline = time.monotonic() + 0.01
        while time.monotonic() < deadline:
            status = self._read_reg(0x07)
            if status & 0x20:  # TX_DS
                self._write_reg(0x07, 0x20)
                return True
            if status & 0x10:  # MAX_RT
                self._write_reg(0x07, 0x10)
                self._command(0xE1)
                return False
        return False

    def available(self):
        fifo = self._read_reg(0x17)
        return not bool(fifo & 0x01)

    def read(self):
        data = self._read_payload(32)
        self._write_reg(0x07, 0x40)
        return data

    def power_down(self):
        self._ce.value = False
        self._write_reg(0x00, 0x00)


def descramble(raw):
    result = bytearray(len(raw))
    for i in range(len(raw)):
        b = BIT_REVERSE[raw[i]]
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            result[i] = b ^ SCRAMBLE_B[idx]
        else:
            result[i] = b
    return bytes(result)


def rescramble(descrambled):
    """Reverse the descramble to get raw bytes for transmission."""
    raw = bytearray(len(descrambled))
    for i in range(len(descrambled)):
        idx = ADDR_WIDTH + i
        if idx < len(SCRAMBLE_B):
            b = descrambled[i] ^ SCRAMBLE_B[idx]
        else:
            b = descrambled[i]
        raw[i] = BIT_REVERSE[b]
    return bytes(raw)


def capture_one_command(radio, button_name, timeout=30):
    """Capture the first command packet (4C6D1765...) for a button press."""
    print(f"  Waiting for command packet (press [{button_name}])...")
    radio.configure_rx()

    deadline = time.monotonic() + timeout
    total = 0
    while time.monotonic() < deadline:
        if radio.available():
            raw = radio.read()
            desc = descramble(raw)
            total += 1
            if desc[:4] == COMMAND_HEADER:
                hex_desc = "".join(f"{b:02X}" for b in desc)
                hex_raw = "".join(f"{b:02X}" for b in raw)
                print(f"  Captured! (after {total} packets)")
                print(f"    Descrambled: {hex_desc}")
                print(f"    Raw:         {hex_raw}")
                return raw, desc
    print(f"  Timeout! ({total} packets, none were commands)")
    return None, None


def main():
    print("=" * 72)
    print("  RF Replay Test — Does the LED controller accept replayed packets?")
    print("=" * 72)

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    csn = digitalio.DigitalInOut(board.D8)
    ce = digitalio.DigitalInOut(board.D25)
    radio = NRF24L01(spi, csn, ce)

    try:
        # Step 1: Capture a Power button command
        print("\n  STEP 1: Capture a Power button press")
        print("  Press the POWER button on the remote once.")
        raw_pkt, desc_pkt = capture_one_command(radio, "Power")

        if raw_pkt is None:
            print("\n  Failed to capture. Exiting.")
            return

        # Verify rescramble roundtrip
        rescrambled = rescramble(desc_pkt)
        if rescrambled == raw_pkt:
            print("  Rescramble verified: roundtrip OK")
        else:
            print("  WARNING: Rescramble mismatch!")
            print(f"    Original: {''.join(f'{b:02X}' for b in raw_pkt)}")
            print(f"    Rescram:  {''.join(f'{b:02X}' for b in rescrambled)}")

        # Step 2: Replay the captured packet
        print("\n  STEP 2: Replay test")
        print("  Watch the LED strip. The Pi will now transmit the captured")
        print("  Power packet 50 times (simulating a button press).")
        print("  If the LEDs toggle on/off, replay works!")
        input("  Press Enter to transmit...")

        radio.configure_tx()
        success = 0
        for i in range(50):
            if radio.transmit(raw_pkt):
                success += 1
            time.sleep(0.005)  # ~5ms between packets (similar to remote)

        print(f"  Transmitted 50 packets ({success} TX confirmations)")
        print()

        # Step 3: Ask user if it worked
        print("  Did the LEDs respond? (y/n)")
        response = input("  > ").strip().lower()

        if response == 'y':
            print("\n  SUCCESS! Replay works!")
            print("  The LED controller does NOT enforce rolling codes.")
            print("  We can capture one raw packet per button and replay them.")
            print("\n  STEP 3: Capture all buttons for replay")
            print("  (This will capture raw packets for each button)")

            buttons = [
                "Power", "Fade", "Dimming", "Strobe",
                "Color1", "2-hour", "Color2", "4-hour",
                "Modes", "Deep_Red", "Mint", "Dark_Blue",
                "Red_Prime", "Orange", "Light_Blue", "Violet",
                "Green_Prime", "Golden_Rod", "Cyan", "Purple",
                "Blue_Prime", "Yellow", "Steel_Blue", "Magenta",
                "White_Select",
            ]

            captured = {}
            for idx, button in enumerate(buttons):
                print(f"\n  [{idx+1:2}/{len(buttons)}] {button}")
                raw, desc = capture_one_command(radio, button)
                if raw:
                    captured[button] = raw
                else:
                    print(f"  Skipping {button}")

            # Save captured packets
            if captured:
                with open("rf_replay_packets.py", "w") as f:
                    f.write('"""Raw captured packets for RF replay to LED controller."""\n\n')
                    f.write("CHANNEL = 42\n")
                    f.write(f"NRF24_ADDR = {NRF24_ADDR}\n")
                    f.write("ADDR_WIDTH = 5\n\n")
                    f.write("# Raw packets (as received by nRF24L01+, ready for TX)\n")
                    f.write("BUTTON_PACKETS = {\n")
                    for button in buttons:
                        if button in captured:
                            hex_str = "".join(f"{b:02X}" for b in captured[button])
                            f.write(f'    "{button}": bytes.fromhex("{hex_str}"),\n')
                    f.write("}\n")
                print(f"\n  Saved {len(captured)} button packets to rf_replay_packets.py")

        else:
            print("\n  Replay did NOT work.")
            print("  The LED controller likely enforces rolling codes.")
            print("  Alternative approaches:")
            print("    1. Try transmitting on all 3 channels (21, 42, 64) simultaneously")
            print("    2. Try sending MORE packets (200+) with shorter interval")
            print("    3. Check if the LED strip uses IR control as backup")

            # Try approach 1: multi-channel burst
            print("\n  Trying multi-channel burst (channels 21, 42, 64)...")
            input("  Press Enter to transmit on all channels...")

            radio.configure_tx()
            for ch in [21, 42, 64]:
                radio._write_reg(0x05, ch)
                for _ in range(100):
                    radio.transmit(raw_pkt)
                    time.sleep(0.002)
                print(f"    Channel {ch}: 100 packets sent")

            print("  Did the LEDs respond now? (y/n)")
            response2 = input("  > ").strip().lower()
            if response2 == 'y':
                print("  Multi-channel replay works! Need to transmit on all channels.")
            else:
                print("  Multi-channel also failed. Protocol may use encryption.")

    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
    finally:
        try:
            radio.power_down()
        except Exception:
            pass
        print("  Radio powered down.")


if __name__ == "__main__":
    main()
