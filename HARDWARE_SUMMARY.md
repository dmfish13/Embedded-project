# Enbrighten LED Cafe Lights — Raspberry Pi 5 RF Controller

## Goal

Reverse-engineer the RF protocol of a **Jasco QOBRGBXYZA 25-button membrane
keypad remote** so a Raspberry Pi 5 can intercept button presses and drive
**Enbrighten RGBW LED Cafe Lights** (TM1815B LEDs) accordingly — replacing
the stock controller with custom software.

## Hardware Setup

### Raspberry Pi 5

Dual-SPI architecture:

| Bus  | Device         | Pins                          | Purpose                       |
|------|----------------|-------------------------------|-------------------------------|
| SPI0 | nRF24L01+     | SCK, MOSI, MISO, CSN=D8, CE=D25 | Receive RF from remote     |
| SPI1 | TM1815B LEDs  | GPIO 20 (MOSI)               | Drive RGBW LED string         |

### nRF24L01+ RF Transceiver (SPI0)

- 2.4 GHz ISM band transceiver
- 10 µF decoupling capacitor between VCC and GND for power stability
- Connected via standard SPI0 pins with CE on GPIO 25, CSN on GPIO 8
- The remote uses a **BK2423/XN297** compatible protocol:
  - **Channel 42** (2.442 GHz)
  - **1 Mbps** data rate
  - Scramble Table B with bit reversal (XN297 encoding)
  - Scrambled 5-byte address: `[0x38, 0x72, 0x2D, 0xA8, 0x5E]`

### TM1815B RGBW LED String (SPI1)

- Enbrighten cafe light string with RGBW (White, Red, Green, Blue) LEDs
- TM1815B constant-current driver IC per LED
- **3.3V-to-5V level shifter** required between GPIO 20 and LED data-in
- Stock on-board driver IC disconnected so Pi is sole data source
- SPI bit-bang protocol at **2.0 MHz** (4 SPI bits per data bit):
  - Logic 1 → `0b0001` (LOW 1500 ns, HIGH 500 ns)
  - Logic 0 → `0b0111` (LOW 500 ns, HIGH 1500 ns)
- Requires continuous SPI output thread for reliable latching
- Frame format: C1 (current setting) + C2 (complement) + pixel data

### Jasco QOBRGBXYZA Remote

- 25-button membrane keypad
- Buttons: Power, Fade, Dimming, Strobe, Color1, Color2, 2-hour/4-hour
  timers, Modes, and 16 color buttons (Deep Red, Mint, Red, Orange, Green,
  Blue, Yellow, Cyan, Purple, Magenta, White, etc.)
- Transmits on 2.4 GHz using BK2423/XN297-compatible protocol

## What Has Been Accomplished

### RF Protocol Reverse Engineering

1. **Channel and data rate identified**: Channel 42, 1 Mbps
2. **Address and scrambling decoded**: XN297 Scramble Table B, bit reversal,
   5-byte address `[0x38, 0x72, 0x2D, 0xA8, 0x5E]`
3. **Command packet header found**: All buttons share a 14-byte descrambled
   header starting with `4C6D176547EC002D48FBDFD11649`
4. **Rolling code confirmed**: Bytes 14–31 are encrypted/rolling — completely
   random per packet with no per-button pattern. Individual button
   identification from payload content alone is not currently possible.
5. **200+ packets analyzed** across 17+ buttons confirming all share the same
   header with encrypted rolling code.

### LED Driver

- TM1815B protocol fully decoded and implemented
- Reliable output requires continuous SPI thread (single-shot writes fail)
- Working test scripts: `led_marquee_cycle_test.py` (full animation suite),
  `led_controller.py` (clean driver class)
- Color definitions for all 25 remote buttons stored in `button_map.py`

## Current Challenge

**Reliably detecting button presses from the remote.**

Since the rolling code prevents identifying *which* button is pressed from
packet content, the current approach is **promiscuous mode RF scanning**:

- nRF24L01+ configured with 2-byte address (widest capture), no CRC, 1 Mbps
- Two receive pipes: `0x00,0x55` and `0x00,0xAA` (catch both preamble types)
- Detect button presses as **packet-rate spikes** above the ambient noise floor
- Adaptive threshold: rolling average of packet rate + margin

### Results So Far

- **Noise floor**: ~30–42 packets/second of background RF noise
- **Button hold**: reliably detected (continuous stream of extra packets)
- **Button taps**: partially detected — quick taps sometimes don't produce
  enough extra packets to exceed the adaptive threshold
- **False positives**: zero when threshold is set high enough, but high
  threshold misses quick taps

### What Comes Next

1. **Improve tap detection sensitivity** — tune the adaptive threshold so
   quick single-tap presses are reliably caught without false positives
2. **Once detection is reliable**, integrate with LED strip to change colors
   on each detected press (cycle through White → Red → Blue → Green)
3. **Button identification** — if individual buttons can't be distinguished
   by payload, explore alternatives (RF replay attack, multi-channel analysis,
   or accept single "any button pressed" trigger for cycling colors)
4. **Final integration** — connect working RF detection + LED control in
   `main.py` for a complete remote-to-lights system

## File Overview

| File | Purpose |
|------|---------|
| `main.py` | Main controller (awaiting RF hex payloads) |
| `led_controller.py` | TM1815B LED strip driver class |
| `led_marquee_cycle_test.py` | LED animation suite (proven working) |
| `button_map.py` | 25-button WRGB color mapping |
| `rf_scanner_reset.py` | Radio state recovery after crashes |
| `rf_promisc_scan.py` | Current: promiscuous RF scanner |
| `rf_promisc_led.py` | Promiscuous scanner + LED feedback |
| `rf_led_detect_test.py` | Previous: specific-address + LED test |
| `rf_replay_test.py` | RF replay attack tester |
| `rf_multichannel_capture.py` | Multi-channel capture script |
| `rf_tap_capture.py` | Button tap payload capture |
| `xn297_descramble.py` | XN297 descrambling utility |
