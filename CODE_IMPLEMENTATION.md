# Code Implementation Guide

## Enbrighten LED Cafe Lights — RF Remote Controller

This document walks through how the project code is implemented: every function, every data structure, every protocol detail, and why each piece works the way it does.

---

## Table of Contents

1. [File Overview](#1-file-overview)
2. [Startup and Initialization Flow](#2-startup-and-initialization-flow)
3. [LED Encoding Pipeline](#3-led-encoding-pipeline)
4. [Frame Construction](#4-frame-construction)
5. [nRF24L01+ Radio Driver](#5-nrf24l01-radio-driver)
6. [XN297L Descramble Pipeline](#6-xn297l-descramble-pipeline)
7. [Pairing and Learning Mode](#7-pairing-and-learning-mode)
8. [Threading Model](#8-threading-model)
9. [Animation Implementations](#9-animation-implementations)
10. [Button Dispatch State Machine](#10-button-dispatch-state-machine)
11. [RF Receive Loop](#11-rf-receive-loop)
12. [Shutdown Sequence](#12-shutdown-sequence)
13. [Supporting Data Modules](#13-supporting-data-modules)

---

## 1. File Overview

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | 1385 | Complete controller — radio, LEDs, animations, button dispatch |
| `remote_button_map.py` | 152 | Pure data: color definitions, button mappings, mode presets |
| `button_map.py` | 198 | Original 25-button map with WRGB values and hex placeholders |
| `led_controller.py` | 183 | Standalone `LEDStrip` class (reference driver, not used by main.py) |
| `button_codes.json` | — | Runtime artifact: button name → captured hex code (created by `--learn`) |
| `SYSTEM_ARCHITECTURE.md` | 632 | Hardware and design rationale documentation |

`main.py` is entirely self-contained — it does not import from the other Python files. All LED encoding, RF protocol handling, color definitions, and animation logic are defined inline so the controller runs from a single file with no local dependencies.

---

## 2. Startup and Initialization Flow

Entry point: `main()` at line 801. The initialization sequence is:

### Step 1 — Radio Hardware (SPI0)

```
spi_radio = busio.SPI(board.SCK, board.MOSI, board.MISO)
csn = digitalio.DigitalInOut(board.D8)
ce = digitalio.DigitalInOut(board.D25)
radio = NRF24L01(spi_radio, csn, ce)
```

Uses **Adafruit Blinka** (`board`, `busio`, `digitalio`) because the nRF24L01+ requires manual chip-select (CSN) toggling around each SPI transaction, which Blinka's `busio.SPI` supports natively. The radio occupies **SPI bus 0** (default Pi 5 GPIO pins 9/10/11).

**Why Blinka instead of spidev for the radio:** The nRF24L01+ protocol requires asserting CSN (chip select) LOW before each register operation and HIGH after. Blinka's `busio.SPI` exposes `try_lock()` and manual `DigitalInOut` for CSN, making multi-byte register reads/writes clean. The `spidev` library's built-in chip select doesn't give the same transaction-level control needed for the nRF24's register protocol.

### Step 2 — LED Hardware (SPI1)

```
spi_led = SpiDev()
spi_led.open(1, 0)
spi_led.max_speed_hz = LED_SPI_SPEED  # 2,000,000 Hz
spi_led.mode = 0b00
```

Uses **spidev** because the TM1815B protocol requires gap-free DMA transfers — spidev on SPI bus 1 produces clean, uninterrupted byte streams at 2.0 MHz. SPI1 uses **GPIO 20** (MOSI) for the LED data line.

**Why spidev instead of Blinka for LEDs:** The TM1815B interprets any gap >200µs as a reset/latch signal. Blinka's `busio.SPI` introduces inter-byte pauses at high speeds. Linux's spidev uses DMA transfers that produce a continuous bitstream — critical for the timing-sensitive LED protocol.

**Why SPI1, not SPI0:** Both peripherals run simultaneously — SPI0 handles the radio, SPI1 handles the LEDs. This dual-bus architecture avoids contention and allows the SPI output thread to refresh LEDs continuously without blocking radio reads.

### Step 3 — Button Code Loading

```
button_codes = load_button_codes()
learn_mode = "--learn" in sys.argv
```

Reads `button_codes.json` if it exists. This JSON file maps button names to their captured RF hex codes (e.g., `{"Power": "A1B2C3D4E5", ...}`). If the file doesn't exist and `--learn` isn't passed, the controller enters a degraded mode where received packets are logged but not dispatched.

### Step 4 — Remote Pairing

```
radio.configure()
pair_remote(radio)
```

Configures the radio registers and waits for the remote to transmit, confirming it's present. See [Section 7](#7-pairing-and-learning-mode) for details.

### Step 5 — Reverse Lookup Map

```
hex_to_button = {v: k for k, v in button_codes.items()}
```

Inverts the button_codes dictionary so the receive loop can map a received hex code directly to a button name in O(1) time.

### Step 6 — State Initialization

Controller state lives as closure variables inside `main()`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `dimmer_idx` | 9 | Index into `DIMMER_STEPS` (9 = 1.0 = full brightness) |
| `current_wrgb` | None | Current solid color tuple, or None when off |
| `active_mode` | None | Active animation name or None |
| `modes_idx` | 0 | Current position in `MODES_LIST` cycle |
| `white_idx` | 2 | Position in `WHITE_TEMPS` (2 = Neutral White) |
| `power_on` | False | Master power state |
| `saved_setting` | None | State snapshot for power-off restore |
| `pending_slot` | None | `'color1'`/`'color2'` when awaiting a color pick |
| `dual_color1/2` | None | WRGB tuples for dual-color mode |
| `timer_thread` | None | Reference to auto-off timer thread |
| `timer_cancel` | Event() | Cancellation signal for timer |

All animation threads and the `handle_button()` function access these via `nonlocal` declarations. The shared frame buffer (`state['buf']`) is protected by a `threading.Lock`.

### Step 7 — Thread Launch and Receive Loop

```
spi_thread = threading.Thread(target=spi_loop, daemon=True)
spi_thread.start()
```

The SPI output thread starts running immediately. The main thread then enters the RF receive loop (an infinite `while True` polling at 1 ms intervals).

---

## 3. LED Encoding Pipeline

### The Problem

The TM1815B LED chip uses a single-wire return-to-zero (RZ) protocol where data is encoded as LOW pulses of varying width on a line that idles HIGH:

- **Logic 1:** LOW for ~1500 ns, then HIGH for ~500 ns
- **Logic 0:** LOW for ~500 ns, then HIGH for ~1500 ns
- **Reset:** Line held HIGH for ≥200 µs (interpreted as "latch data")

The Raspberry Pi has no hardware peripheral for this protocol. The solution is **SPI bit-banging**: each data bit is mapped to 4 SPI bits at 2.0 MHz (500 ns per SPI bit), so 4 SPI bits = 2000 ns = one data bit period.

### Encoding Table

| Data Bit | SPI Bits | SPI Line Behavior | Timing |
|----------|----------|-------------------|--------|
| 1 | `0001` | LOW-LOW-LOW-HIGH | 1500 ns LOW, 500 ns HIGH |
| 0 | `0111` | LOW-HIGH-HIGH-HIGH | 500 ns LOW, 1500 ns HIGH |

SPI idles with MOSI LOW (mode 0b00), and the TM1815B line idles HIGH. Since the SPI line polarity is inverted relative to the LED protocol, the encoding inverts accordingly — a `0` SPI bit produces a LOW on the wire.

### `encode_byte_4bit(value)` — Line 313

Encodes one byte (0–255) into 4 SPI bytes (32 SPI bits):

```python
def encode_byte_4bit(value):
    encoded = 0
    for bit_pos in range(7, -1, -1):    # MSB first
        if value & (1 << bit_pos):
            encoded = (encoded << 4) | 0b0001  # Data 1
        else:
            encoded = (encoded << 4) | 0b0111  # Data 0
    return bytes([
        (encoded >> 24) & 0xFF, (encoded >> 16) & 0xFF,
        (encoded >> 8) & 0xFF, encoded & 0xFF,
    ])
```

Processes bits from MSB (bit 7) to LSB (bit 0), building a 32-bit integer that is then split into 4 bytes for SPI transmission.

### `LUT_4BIT` — Line 328

```python
LUT_4BIT = [encode_byte_4bit(v) for v in range(256)]
```

Pre-computes the 4-byte SPI encoding for every possible byte value (0–255) at import time. This 256-entry lookup table eliminates all per-frame bit manipulation. During frame construction, encoding a color byte is a single list index: `LUT_4BIT[value]`.

**Why a LUT:** The SPI output thread runs at ~500 frames/second. Without the LUT, each frame would require 24 bytes × 6 LEDs = 144 calls to `encode_byte_4bit()`, each with 8 loop iterations. The LUT reduces this to 144 table lookups — roughly 100× faster in Python.

### SPI Clock Rate Selection

The exact data rate for TM1815B is 400 kHz (2.5 µs per bit). With 4 SPI bits per data bit, the ideal SPI clock is 1.6 MHz. However, the Raspberry Pi 5's SPI peripheral introduces inter-byte gaps at non-standard clock dividers. At 1.6 MHz, these gaps corrupt the protocol timing.

**2.0 MHz** was chosen because it hits a clean clock divider (250 MHz core / 125 = 2.0 MHz) that produces **gap-free DMA transfers**. The slightly faster clock (500 ns per SPI bit vs. the ideal 625 ns) is within the TM1815B's timing tolerance:

- Logic 1 at 2.0 MHz: 1500 ns LOW (spec: 1300–2000 ns) ✓
- Logic 0 at 2.0 MHz: 500 ns LOW (spec: 620–820 ns) — slightly fast, but reliable in practice

---

## 4. Frame Construction

### TM1815B Frame Structure

A complete frame sent to the LED string:

```
[RESET: 80 × 0xFF] [C1: 4B] [C2: 4B] [D1: 16B] [D2: 16B] ... [D6: 16B] [RESET: 80 × 0xFF]
```

| Field | Raw Bytes | SPI Bytes | Purpose |
|-------|-----------|-----------|---------|
| RESET (lead) | 80 × 0xFF | 80 | >200 µs idle HIGH to reset state machine |
| C1 | 4 | 16 | Current-setting register (6-bit per channel) |
| C2 | 4 | 16 | Bitwise complement of C1 (error detection) |
| D1–D6 | 4 each | 16 each | Pixel data (W, R, G, B) per LED |
| RESET (trail) | 80 × 0xFF | 80 | Latch pulse |

**Total per frame:** 80 + 16 + 16 + (16 × 6) + 80 = **288 SPI bytes** (not counting the 80-byte resets which are raw 0xFF).

Wait — the RESET bytes are sent as literal 0xFF (not SPI-encoded), and the C1/C2/pixel bytes go through the LUT. So the actual buffer size is: 80 + (4 × 4) + (4 × 4) + (6 × 4 × 4) + 80 = 80 + 16 + 16 + 96 + 80 = **288 bytes** minimum. In practice the frame is ~576 bytes due to the 3× repeated transmission (see `show()` discussion).

### Current-Setting Registers (C1/C2)

```python
C1 = [0x20, 0x20, 0x20, 0x20]  # 50% of max 30mA per channel
C2 = [0xDF, 0xDF, 0xDF, 0xDF]  # Bitwise complement
```

Each TM1815B IC can sink up to 30 mA per WRGB channel. The C1 register's 6 low bits (0x20 = 0b00100000, but masked to 6 bits = 0x20 & 0x3F = 0x20 = 32/63 ≈ 50%) set the maximum current. C2 = `C1[i] ^ 0xFF` acts as a checksum — if C1 and C2 don't match, the IC ignores the frame.

**Why 50% current:** Full 30 mA × 4 channels × 6 LEDs = 720 mA draws significant power from the cafe light string's supply. 50% current (15 mA per channel) provides sufficient brightness while staying well within the power supply's limits and reducing heat.

### `build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes)` — Line 331

```python
def build_frame(c1_bytes, c2_bytes, pixels, lut, reset_bytes=80):
    buf = bytearray(b'\xFF' * reset_bytes)
    for bv in c1_bytes:
        buf += lut[bv]
    for bv in c2_bytes:
        buf += lut[bv]
    for w, r, g, b in pixels:
        buf += lut[w] + lut[r] + lut[g] + lut[b]
    buf += b'\xFF' * reset_bytes
    return buf
```

Constructs the complete SPI byte stream. The `pixels` list contains (W, R, G, B) tuples already scaled by the dimmer. Each color value is encoded via the LUT.

**Color order:** The TM1815B expects White first, then Red, Green, Blue. All color tuples throughout the codebase follow WRGB order to match the wire format directly.

### Helper Functions

**`dim_pixel(wrgb, dimmer)`** — Scales each channel by the dimmer factor (0.0–1.0) using integer multiplication. Applied before frame construction so the LUT receives the final brightness value.

**`lerp_pixel(a, b, t)`** — Linear interpolation between two WRGB tuples. Used by fade and twinkle animations. When `t=0` returns `a`, when `t=1` returns `b`, values between produce smooth transitions.

**`build_solid_buf(wrgb, dimmer)`** — Convenience: dims the color and fills all 6 LEDs. Returns a `list` (not `bytearray`) because `spidev.xfer2()` requires a list.

**`build_multi_buf(pcb_pixels)`** — Takes a list of 6 individually-addressed WRGB tuples. Used by chaser, theater, twinkle, and preset modes.

**`build_dual_buf(color1, color2, dimmer)`** — Alternates two colors across LEDs: odd-index PCBs get color1, even-index get color2. Used by the Color1/Color2 dual-color mode.

---

## 5. nRF24L01+ Radio Driver

### Class: `NRF24L01` — Line 413

A minimal RX-only driver for the nRF24L01+ transceiver. Only implements the operations needed to receive packets from the Jasco remote — no TX capability.

### Pin Wiring

| Signal | GPIO | Purpose |
|--------|------|---------|
| SCK | GPIO 11 (default SPI0) | SPI clock |
| MOSI | GPIO 10 | SPI data out (commands to radio) |
| MISO | GPIO 9 | SPI data in (responses from radio) |
| CSN | GPIO D8 | Chip select (active LOW, manually toggled) |
| CE | GPIO D25 | Chip enable (HIGH = RX active) |

### SPI Transaction Protocol

Every register read/write follows the same pattern:

1. Acquire SPI bus lock (`try_lock()`)
2. Configure clock to 1 MHz
3. Pull CSN LOW (select the radio)
4. Transfer bytes
5. Pull CSN HIGH (deselect)
6. Release lock

The nRF24L01+ clocks in a command byte on the rising edge of SCK while CSN is LOW. The first byte sent is always the command/register address. Response data appears on MISO starting from the second byte.

### Register Methods

**`_reg_read(reg)`** — Sends a 2-byte transaction: `[reg_addr, 0xFF]`. The radio returns its status byte on the first byte and the register value on the second. Returns `buf[1]`.

**`_reg_write(reg, value)`** — Sends `[0x20 | reg, value]`. The `0x20` prefix is the nRF24L01+'s "write register" command.

**`_reg_write_bytes(reg, data)`** — Multi-byte register write (used for the 5-byte RX address). Sends `[0x20 | reg, data[0], data[1], ...]`.

**`_cmd(cmd)`** — Single command byte with no data payload (used for FLUSH_RX `0xE2` and FLUSH_TX `0xE1`).

**`_read_payload(length)`** — Reads the RX FIFO. Sends `[0x61, 0xFF, 0xFF, ...]` (command `R_RX_PAYLOAD` followed by dummy bytes to clock out the data). Returns bytes 1–32 (strips the status byte).

### `configure()` — Line 482

Sets up the radio for XN297L-compatible reception:

```
Register  Value   Meaning
───────────────────────────────────────────────────
0x00      0x03    CONFIG: PWR_UP=1, PRIM_RX=1 (power on, RX mode)
0x01      0x00    EN_AA: auto-ack disabled (XN297L doesn't use it)
0x02      0x01    EN_RXADDR: only pipe 0 enabled
0x03      0x03    SETUP_AW: 5-byte address (ADDR_WIDTH - 2)
0x0A      [addr]  RX_ADDR_P0: [0x38, 0x72, 0x2D, 0xA8, 0x5E]
0x11      32      RX_PW_P0: 32-byte fixed payload
0x06      0x07    RF_SETUP: 1 Mbps, 0 dBm TX power
0x1C      0x00    DYNPD: dynamic payload disabled
0x1D      0x00    FEATURE: all features off
0xE2      —       FLUSH_RX: clear receive FIFO
0xE1      —       FLUSH_TX: clear transmit FIFO
0x07      0x70    STATUS: clear all interrupt flags
0x05      42      RF_CH: channel 42 (2442 MHz)
```

After configuration, CE is driven HIGH to enter active RX mode. The 2 ms delay after PWR_UP allows the radio's crystal oscillator to stabilize.

**Why no CRC:** The XN297L protocol uses its own scrambling and doesn't generate nRF24L01+-compatible CRC checksums. Enabling CRC would cause every packet to fail the check.

**Why no auto-ack:** Auto-acknowledge requires the receiver to transmit back to the sender. The Jasco remote doesn't expect acknowledgments — it's a fire-and-forget broadcast protocol.

**Why 32-byte fixed payload:** The XN297L always sends full 32-byte frames. Using dynamic payload length would require features (FEATURE register) that don't interoperate with the XN297L protocol.

### `available()` — Line 513

Checks bit 0 of the FIFO_STATUS register (0x17). If `RX_EMPTY` is 0, there's data waiting. This is polled at ~1000 Hz by the main receive loop.

### `read()` — Line 518

Reads the 32-byte payload, then clears all interrupt flags (STATUS register 0x07 = 0x70). Clearing flags is necessary because the nRF24L01+ won't trigger a new RX_DR interrupt until the previous one is acknowledged.

### `power_down()` — Line 524

Drops CE LOW and clears PWR_UP in the CONFIG register. The radio enters a ~900 nA standby mode. Called during shutdown.

---

## 6. XN297L Descramble Pipeline

The Jasco remote's XNS1042 (XN297L protocol) transmitter applies two transformations before sending data over the air:

1. **Bit reversal:** Each byte's bit order is flipped (MSB becomes LSB)
2. **XOR scrambling:** Each byte is XOR'd with a corresponding entry in Scramble Table B

The nRF24L01+ receives the raw on-air bytes. To recover the original payload, these transformations must be undone.

### `BIT_REVERSE` — Line 145

```python
BIT_REVERSE = bytes([int(f"{i:08b}"[::-1], 2) for i in range(256)])
```

A 256-entry lookup table that reverses the bit order of any byte. For example: `BIT_REVERSE[0xC0]` (binary `11000000`) = `0x03` (binary `00000011`).

**Why a LUT:** Bit reversal in Python without a LUT requires string formatting or bit manipulation loops — both slow when called hundreds of times per second. The 256-byte table makes it a single index operation.

### `SCRAMBLE_B` — Line 149

The 32-byte XOR key used by the XN297L protocol. Bytes 0–4 correspond to the address, bytes 5–31 to the payload. Since the nRF24L01+ strips the address before delivering the payload, descrambling starts at offset `ADDR_WIDTH` (5).

### `descramble(raw)` — Line 538

```python
def descramble(raw):
    result = bytearray(len(raw))
    for i in range(len(raw)):
        b = BIT_REVERSE[raw[i]]              # Step 1: reverse bits
        idx = ADDR_WIDTH + i                   # Step 2: scramble table offset
        if idx < len(SCRAMBLE_B):
            result[i] = b ^ SCRAMBLE_B[idx]    # Step 3: XOR descramble
        else:
            result[i] = b                       # Beyond table: bit-reverse only
    return bytes(result)
```

For each byte in the raw payload:
1. Look up the bit-reversed value in `BIT_REVERSE`
2. XOR with `SCRAMBLE_B[5 + i]` (the scramble table entry for this payload position)
3. Bytes beyond the scramble table's 32 entries are only bit-reversed (no XOR)

### `to_raw(desc_bytes)` — Line 556

The inverse of `descramble()` — converts descrambled bytes back to the on-air format. Used by `find_idle_start()` to detect idle-line tail patterns that are recognizable in the raw (pre-descramble) domain.

### `find_idle_start(desc_bytes)` — Line 578

The XN297L transmits fixed 32-byte frames regardless of actual data length. Unused bytes at the end contain idle-line fill patterns: raw values `0xFF`, `0x55`, or `0xAA` (alternating high/low bit patterns from the idle transmitter).

This function identifies where the meaningful payload ends:

1. Convert descrambled bytes back to raw format via `to_raw()`
2. Check the last byte — if it's not an idle pattern (0xFF/0x55/0xAA), all bytes are data
3. Scan backward from the end, marking bytes that match the tail value (with ±1 bit error tolerance for RF noise)
4. Return the index where the tail begins = length of meaningful data

**Why ±1 bit tolerance:** RF noise can flip a single bit in the idle tail, turning `0xFF` into `0xFE` or `0x55` into `0x57`. The `bin(raw[i] ^ tail_val).count('1') <= 1` check allows these near-matches so the tail boundary isn't misidentified.

### `is_background_packet(desc)` — Line 603

An unidentified transmitter shares the same RF address and channel, sending packets at ~30 per second with two distinct 4-byte headers:

- `COMMAND_HEADER = bytes([0x4C, 0x6D, 0x17, 0x65])`
- `IDLE_HEADER = bytes([0x4C, 0xED, 0xAD, 0x0B])`

This function checks the first 4 descrambled bytes against both patterns. Matching packets are silently discarded.

### `extract_button_code(desc)` — Line 613

Combines `find_idle_start()` and `to_hex()` to produce the canonical hex string identifying a button press. The idle tail is stripped so different-length transmissions of the same button produce the same code.

---

## 7. Pairing and Learning Mode

### `pair_remote(radio)` — Line 662

"Pairing" confirms the remote is present and transmitting. The actual security mechanism is the nRF24L01+'s hardware address filter — it only accepts packets matching `[0x38, 0x72, 0x2D, 0xA8, 0x5E]`. No software pairing is needed to exclude other devices.

The pairing algorithm:

1. Configure the radio and start listening
2. Poll for packets, discarding known background source packets
3. On the first non-background packet, start a 5-second confirmation window
4. If a second non-background packet arrives within 5 seconds, pairing is confirmed
5. If no second packet arrives, prompt the user to try again

**Why two packets:** A single packet could be a transient noise hit that happened to pass the address filter. Two packets within 5 seconds confirms an active transmitter.

### `learn_buttons(radio)` — Line 736

Interactive mode (`--learn` flag) that captures the RF hex code for each of the 25 buttons:

For each button in `BUTTON_NAMES`:
1. Prompt the user to press and hold the button
2. Capture all non-background packets for 2 seconds
3. Extract hex codes from each packet via `extract_button_code()`
4. Use **majority vote** — the most frequently seen hex code becomes that button's canonical code

```python
code_counts = {}
for c in captured:
    code_counts[c] = code_counts.get(c, 0) + 1
best_code = max(code_counts, key=code_counts.get)
```

**Why majority vote:** RF noise can corrupt individual packets, producing occasional wrong hex codes. Over a 2-second window the remote sends dozens of identical packets. The most common code is overwhelmingly likely to be correct.

Results are saved to `button_codes.json` via `save_button_codes()`. Previously learned buttons can be re-learned by answering 'y' at the prompt.

### `load_button_codes()` / `save_button_codes()` — Lines 632/643

Simple JSON I/O for the button code mapping. The file path is computed relative to `main.py`'s location:

```python
BUTTON_CODE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "button_codes.json")
```

---

## 8. Threading Model

The controller uses four thread types, all created as daemon threads so they exit automatically if the main thread is interrupted:

### Thread 1: Main Thread (RF Receive Loop)

The main thread runs the RF polling loop at ~1000 Hz. It handles:
- Reading packets from the radio
- Descrambling
- Background packet filtering
- Debouncing
- Button dispatch via `handle_button()`

This is the only thread that reads from the radio.

### Thread 2: SPI Output Thread (`spi_loop`)

```python
def spi_loop():
    while state['running']:
        with lock:
            buf = state['buf']
        spi_led.xfer2(buf[:])
```

Continuously pushes the current frame buffer to SPI1 as fast as possible. At 2.0 MHz with ~288 bytes per frame, this achieves approximately **500 frames/second**.

**Why continuous refresh:** The TM1815B LEDs require constant re-transmission to maintain output. Without refresh, the LEDs may dim or flicker. The continuous push ensures rock-solid output.

**Locking:** The buffer reference is copied under lock (`buf = state['buf']`), then sent outside the lock. This allows animation threads to prepare new buffers without blocking the SPI output. The `buf[:]` slice creates a copy so the transmitted data doesn't change mid-transfer if the buffer reference is swapped by another thread between the lock release and the `xfer2` call.

### Thread 3: Mode Thread (Animations)

A single animation thread runs the active effect (fade, strobe, chaser, theater, twinkle, or preset). Only one mode thread exists at a time.

**Lifecycle:**
1. `start_mode(mode_name)` calls `stop_mode()` to terminate the previous animation
2. `stop_mode()` sets `mode_stop` Event, then `join(timeout=2)` waits for the thread to exit
3. `mode_stop` is cleared, and a new daemon thread is launched for the new mode

**Stopping mechanism:** All animation loops check `mode_stop.is_set()` at every iteration and at sleep points. Instead of `time.sleep(interval)`, animations use `mode_stop.wait(interval)` — this returns `True` immediately if the stop event is set, providing sub-millisecond stop response without polling.

```python
# Bad: slow to stop (sleeps the full interval before checking)
time.sleep(CHASER_INTERVAL)
if mode_stop.is_set(): return

# Good: returns immediately when stop event is set
if mode_stop.wait(CHASER_INTERVAL): return
```

### Thread 4: Timer Thread (Auto-Off)

Created by `start_timer(hours, label)`. Uses the same event-based pattern:

```python
def _wait():
    if not timer_cancel.wait(hours * 3600):
        timer_fire()
```

If `timer_cancel` is set before the timeout expires, the timer is cancelled (the `wait()` returns `True` and `timer_fire()` is never called). If the timeout expires naturally, `timer_fire()` saves the current state and powers off.

---

## 9. Animation Implementations

### Fade (`fade_loop`) — Line 886

Smooth crossfade between random colors:

1. Start with `start_color` (the current solid color, or random)
2. Pick a new random target color (excluding current to avoid "fading to same")
3. Interpolate over `FADE_DURATION` (1.5s) in steps of `FADE_STEP_INTERVAL` (30ms) — ~50 steps
4. Hold the target color for `FADE_HOLD` (0.5s)
5. Repeat from step 2

Each interpolation step calls `lerp_pixel(current, target, t)` where `t` goes from 0.0 to 1.0, then writes the result to the shared buffer. The hold period uses `mode_stop.wait()` for instant cancellation.

### Strobe (`strobe_loop`) — Line 906

Hard-cut color changes at `STROBE_INTERVAL` (1.25s):

```python
while not mode_stop.wait(STROBE_INTERVAL):
    current = pick_random(exclude=current)
    with lock:
        state['buf'] = build_solid_buf(current, 1.0)
```

No fade — each step instantly switches all LEDs to a new random color. The `mode_stop.wait()` serves as both the delay and the cancellation check.

### Chaser (`chaser_loop`) — Line 919

Colors shift through the LED chain like a marquee:

1. Initialize each of 6 LEDs with a random color
2. Every `CHASER_INTERVAL` (0.35s):
   - Shift all colors right: `pcb_colors[5] ← [4] ← [3] ← [2] ← [1] ← [0]`
   - Assign a new random color to `pcb_colors[0]`
3. Apply current dimmer level and rebuild the frame

The chaser respects the dimmer setting in real-time — `dim = DIMMER_STEPS[dimmer_idx]` is re-read each tick, so pressing Dimming during a chaser takes effect immediately.

### Theater Chase (`theater_loop`) — Line 939

Classic theater marquee pattern:

```
Step 0: [ON  OFF OFF ON  OFF OFF]
Step 1: [OFF ON  OFF OFF ON  OFF]
Step 2: [OFF OFF ON  OFF OFF ON ]
Step 3: [ON  OFF OFF ON  OFF OFF]  (repeats)
```

Every `THEATER_INTERVAL` (0.3s), the lit position advances by one (modulo 3). Lit LEDs display `NEUTRAL_WHITE`, unlit ones are `OFF`.

### Twinkle (`twinkle_loop`) — Line 956

Each LED independently fades through a sequence of random colors:

**Initialization:**
- Pre-generate `TWINKLE_COLOR_COUNT` (200) random colors per LED
- Assign each LED a random stagger delay (1.0–4.0 seconds)

**Per-LED state machine (4 phases):**

| Phase | Behavior | Duration |
|-------|----------|----------|
| `delay` | Initial stagger — LED holds start color | 1.0–4.0s (random) |
| `fade_down` | Fade current color → OFF (black) | `TWINKLE_FADE_DOWN` (1.25s) |
| `fade_up` | Fade OFF → next color in sequence | `TWINKLE_FADE_UP` (1.25s) |
| `hold` | Hold at full brightness | `TWINKLE_HOLD` (0.5s) |

The main loop ticks every `TWINKLE_TICK` (30ms). Each tick advances elapsed time for all 6 LEDs and computes their current pixel value based on phase + progress. The combined 6-LED frame is rebuilt and written to the buffer each tick.

**Why pre-generated sequences:** Calling `random.choice()` inside the inner loop for 6 LEDs at 33 FPS would add latency. Pre-generating 200 colors per LED at startup (1200 random picks total) makes the tick loop purely arithmetic.

### Preset Static (`preset_static_loop`) — Line 1021

Displays a repeating color pattern:

```python
pixels = [pattern[n % len(pattern)] for n in range(NUM_LEDS)]
```

For example, Christmas: `[WHITE, FOREST_GREEN, DEEP_RED, WHITE, FOREST_GREEN, DEEP_RED]`. The pattern is set once and held until mode_stop is signaled — no animation, just a static display.

---

## 10. Button Dispatch State Machine

### `handle_button(btn_name)` — Line 1118

The central dispatcher for all 25 buttons. Called by the RF receive loop after debouncing. Uses `nonlocal` to modify closure state variables.

### Power Button

Toggle behavior with state save/restore:

**Power OFF (was on):**
1. Save current state to `saved_setting`: color, active mode, dimmer, dual-color settings
2. Cancel any running timer
3. Stop any active animation
4. Set buffer to all-off
5. Set `power_on = False`

**Power ON (was off):**
1. Set `power_on = True`
2. If `saved_setting` exists, restore the previous state:
   - Dual-color mode → rebuild dual buffer
   - Animation mode → restart the animation
   - Solid color → rebuild solid buffer with previous dimmer
3. If no saved setting (first power-on), default to Neutral White at full brightness

### Color Buttons (15 buttons)

Two behaviors depending on whether a `pending_slot` is active:

**With pending_slot** (Color1 or Color2 was pressed previously):
1. Assign the color to `dual_color1` or `dual_color2`
2. Clear `pending_slot`
3. Stop any animation, set `active_mode = 'dual'`
4. Build and display the dual-color buffer

**Without pending_slot** (normal press):
1. Stop any animation
2. Clear dual-color state
3. Set `current_wrgb` to the button's color
4. Build and display solid buffer at current dimmer level

### White Select

Cycles through 5 temperature presets in `WHITE_TEMPS`:
```
Candlelight ~1800K → Warm White ~3000K → Neutral White ~4000K → Cool White ~5000K → Daylight ~6500K
```

Starts at index 2 (Neutral White). Each press increments and wraps. Like color buttons, respects `pending_slot` for dual-color assignment.

### Color1 / Color2

Sets `pending_slot = 'color1'` or `'color2'` and prints a prompt. The next color button or white select press fills that slot.

### 2-hour / 4-hour Timers

Starts a background timer that calls `timer_fire()` after the specified duration. Starting a new timer cancels any existing one. The timer saves state and powers off when it expires.

### Fade

Resets dimmer to full (1.0), then starts the fade animation. If the current state is a solid color, that color becomes the fade's starting point.

### Dimming

Cycles brightness: 1.0 → 0.9 → 0.8 → ... → 0.1 → 1.0 (wraps around). Only applies to solid color, dual-color, and chaser modes. Ignored during fade, strobe, theater, twinkle, and preset (these always run at full brightness).

### Strobe

Same as Fade but starts the strobe animation.

### Modes

Cycles through 7 entries in `MODES_LIST`:

| Index | Name | Type |
|-------|------|------|
| 0 | Christmas | preset (White / Forest Green / Deep Red) |
| 1 | St Patrick's Day | preset (Irish Green / Cool White / Orange) |
| 2 | 4th of July | preset (Red / Cool White / Dark Blue) |
| 3 | Canada | preset (Deep Red / Cool White / Deep Red) |
| 4 | Chaser | chaser animation |
| 5 | Theater Chase | theater animation |
| 6 | Twinkle | twinkle animation |

If already in a modes-type animation, pressing Modes advances to the next entry. If in a different state (solid, fade, strobe, dual), pressing Modes starts at index 0 (Christmas).

---

## 11. RF Receive Loop

The main loop at line 1335:

```python
while True:
    if radio.available():
        raw = radio.read()
        desc = descramble(raw)

        if is_background_packet(desc):
            continue

        code = extract_button_code(desc)
        now = time.monotonic()

        if (code == last_button_code
                and (now - last_button_time) < DEBOUNCE_S):
            continue
        last_button_code = code
        last_button_time = now

        btn_name = hex_to_button.get(code)
        if btn_name:
            handle_button(btn_name)
        else:
            print(f"  Unknown: {code[:48]}...")

    time.sleep(0.001)
```

### Processing Pipeline

For each iteration:

1. **Poll:** Check `radio.available()` (~1000 Hz with the 1ms sleep)
2. **Read:** Pull the 32-byte payload from the RX FIFO
3. **Descramble:** Undo XN297L bit reversal + XOR scrambling
4. **Filter:** Discard known background source packets
5. **Extract:** Strip idle tail, convert to hex string
6. **Debounce:** Ignore if the same code was seen within `DEBOUNCE_S` (300ms)
7. **Lookup:** Map hex code → button name via `hex_to_button` dict
8. **Dispatch:** Call `handle_button()` or log as unknown

### Debounce Strategy

The remote sends repeated packets while a button is held (burst transmission). The debounce logic uses a simple time window:

```python
if (code == last_button_code and (now - last_button_time) < DEBOUNCE_S):
    continue
```

Only the **most recent** code is tracked. This means:
- Holding one button: first press is processed, subsequent packets within 300ms are ignored
- Pressing a different button: immediately processed (different code)
- Pressing the same button again after 300ms: processed (outside debounce window)

**Why 300ms:** Fast enough to feel responsive (human button presses are ~200ms minimum), but long enough to filter the remote's burst of 5–15 identical packets per press.

---

## 12. Shutdown Sequence

Triggered by `KeyboardInterrupt` (Ctrl+C):

```python
cancel_timer()          # Stop auto-off timer
mode_stop.set()         # Signal animation thread to exit
state['running'] = False # Signal SPI output thread to exit
spi_thread.join(timeout=1)
if mode_thread:
    mode_thread.join(timeout=1)
# Send final "all off" frame
with lock:
    state['buf'] = build_solid_buf(None, 1.0)
spi_led.xfer2(state['buf'][:])
radio.power_down()      # CE LOW, PWR_UP=0 (~900 nA)
spi_led.close()         # Release SPI1 device file
```

The order matters:
1. Cancel timer first (it could fire during shutdown and try to modify state)
2. Stop animation thread (it writes to the shared buffer)
3. Stop SPI output thread (it reads the shared buffer)
4. Send one final all-off frame directly (bypass the now-stopped output thread)
5. Power down the radio to minimize current draw
6. Close the SPI device file descriptor

---

## 13. Supporting Data Modules

### `remote_button_map.py`

A pure data module with no hardware dependencies. Contains all color definitions, button mappings, mode presets, and white temperature variants extracted from `main.py`. Intended for use when the codebase is split into multiple files.

Key data structures:
- **Color constants:** 15 direct colors + 4 white temperatures + 2 holiday colors, all as WRGB tuples
- **`COLOR_BUTTONS`:** Dict mapping button names → WRGB tuples for the 15 direct-color buttons
- **`ELIGIBLE_COLORS`:** List of colors used for random selection in animations (excludes white temperatures)
- **`COLOR_NAMES`:** Display names for console output
- **`WHITE_TEMPS`:** 5 temperature presets ordered warm→cool, cycled by the White button
- **`MODES_LIST`:** 7-entry cycle: 4 holiday presets + 3 animation modes
- **`BUTTON_NAMES`:** All 25 button names in remote layout order (used by `--learn`)

### `button_map.py`

The original button map with placeholder hex values. Each of the 25 buttons has:
- `label`: Human-readable name
- `hex`: Placeholder `None` (to be filled by RF capture)
- `wrgb`: Default WRGB tuple for color buttons; `None` for function buttons

Includes utility functions:
- `lookup_by_hex(payload_hex)`: Find a button entry by captured payload
- `get_wrgb(button_key)`: Get the WRGB tuple for a button name
- `list_buttons()`: Print all buttons in a formatted table

### `led_controller.py`

A standalone `LEDStrip` class for TM1815B LEDs. Uses the same SPI bit-banging approach as `main.py` but with a different API:
- `set_pixel(index, r, g, b, w)` — Note: RGBW order (not WRGB)
- `set_all(r, g, b, w)` — Fill all LEDs
- `show()` — Push buffer to SPI (sends 3× for reliability)
- `fill_color(rgbw_tuple)` — Set all + show in one call
- Built-in brightness scaling (0.0–1.0)

This file is a reference implementation. `main.py` reimplements the LED encoding inline (with WRGB order matching the TM1815B wire format) to avoid an external dependency.
