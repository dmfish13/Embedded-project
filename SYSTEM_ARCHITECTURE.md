# Enbrighten RGBW LED Cafe Lights — RF Remote Controller
## System Architecture & Design Document

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [RF Signal Interception](#rf-signal-interception)
3. [Dual-Bus Hardware Architecture](#dual-bus-hardware-architecture)
4. [Non-Blocking State Machine Software Design](#non-blocking-state-machine-software-design)
5. [Logic Level Optimization](#logic-level-optimization)
6. [Design Decisions & Rationale](#design-decisions--rationale)

---

## Project Overview

This project replaces the original Enbrighten LED cafe light controller IC with a Raspberry Pi 5, intercepting the RF signal from the original Jasco QOBRGBXYZA 25-button remote control and driving the TM1815B RGBW LED string directly. The system operates as a transparent replacement — the user presses buttons on the same physical remote, and the lights respond identically (plus additional modes not available in the stock controller).

### Why Build This?

The stock Enbrighten controller has limited functionality: basic color selection, a single white temperature, and minimal animation modes. By intercepting the RF signal and driving the LEDs directly, we gain:

- Full 32-bit WRGB color control per LED (vs. the stock controller's limited palette)
- Custom animation algorithms (twinkle, theater chase, color chaser)
- Holiday presets with arbitrary color patterns
- 10-step dimming (vs. stock 4-step)
- Dual-color mode (alternating odd/even LEDs)
- Programmable auto-off timers
- Software-updatable — new features without hardware changes

### System Block Diagram

```
    ┌──────────────────────────────────────────────────────────────────────┐
    │                         Raspberry Pi 5                                │
    │                                                                       │
    │  ┌───────────────┐         main.py            ┌───────────────────┐  │
    │  │   nRF24L01+   │◄──── SPI0 (1 MHz) ───────►│   Main Thread     │  │
    │  │   RF Receiver │      GPIO D8 (CSN)          │   (RF poll +      │  │
    │  │               │      GPIO D25 (CE)          │    button dispatch)│  │
    │  └───────┬───────┘                             └─────────┬─────────┘  │
    │          │                                               │            │
    │          │ 2.4 GHz                              ┌────────┴─────────┐  │
    │          │ Channel 42                           │  SPI Output      │  │
    │          │ 1 Mbps                               │  Thread           │  │
    │          │                                      │  (~500 FPS        │  │
    │  ┌───────┴───────┐                              │   continuous)     │  │
    │  │  Jasco Remote │                              └────────┬─────────┘  │
    │  │  QOBRGBXYZA   │                                       │            │
    │  │  (25 buttons) │                              SPI1 (2.0 MHz)        │
    │  │  BK2423 TX    │                              GPIO 20 (MOSI)        │
    │  └───────────────┘                                       │            │
    │                                                 ┌────────┴─────────┐  │
    │                                                 │  3.3V → 5V       │  │
    │                                                 │  Level Shifter   │  │
    │                                                 └────────┬─────────┘  │
    │                                                          │            │
    └──────────────────────────────────────────────────────────┼────────────┘
                                                               │
                                                      ┌────────┴─────────┐
                                                      │   TM1815B RGBW   │
                                                      │   LED String     │
                                                      │   (6 LEDs/PCBs   │
                                                      │    daisy-chained)│
                                                      └──────────────────┘
```

---

## RF Signal Interception

### Background: Why RF Interception?

The Jasco remote communicates wirelessly with the LED controller at 2.4 GHz. Rather than designing a custom remote or using a phone app, this project intercepts the existing remote's signal. This preserves the original user experience — the same physical remote, same button layout, same muscle memory — while completely replacing the receiver and LED driver with custom software.

### The BK2423/XN297 Protocol

The remote's transmitter is a **BK2423** (also marketed as XN297), a Chinese-manufactured RF transceiver that is wire-compatible with Nordic Semiconductor's nRF24L01+ at the radio layer but adds two additional encoding steps to the payload before transmission.

**Why the BK2423 exists:** It was designed as a cheaper drop-in replacement for the nRF24L01+ in consumer products (remotes, toys, keyboards). The scrambling was added to provide minimal over-the-air obfuscation without requiring firmware changes on the MCU side — the BK2423 handles it transparently in hardware.

**What this means for interception:** A standard nRF24L01+ receiver can hear BK2423 packets because the physical layer (modulation, preamble, address matching) is identical. However, the received payload bytes must be descrambled in software to recover the original data.

### Descramble Process

The BK2423 applies two transformations before transmitting each payload byte:

1. **Bit Reversal** — Each byte's bit order is flipped (MSB becomes LSB)
2. **XOR with Scramble Table B** — A fixed 32-byte sequence, applied starting at byte offset equal to the address width (5)

To recover the original data:
```
descrambled[i] = BIT_REVERSE_LUT[raw_byte[i]] XOR SCRAMBLE_TABLE_B[5 + i]
```

The `BIT_REVERSE` operation is implemented as a 256-entry lookup table for O(1) per byte — no loop over 8 bits. The Scramble Table B is a fixed constant from the BK2423 datasheet:

```
SCRAMBLE_B = [
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
]
```

### Radio Configuration

| Parameter | Value | Why This Value |
|-----------|-------|----------------|
| Frequency | 2442 MHz (Channel 42) | Discovered via channel scanning; the remote transmits here |
| Data Rate | 1 Mbps | BK2423's default mode; matches nRF24L01+ standard rate |
| Address | `38 72 2D A8 5E` (5 bytes) | Reverse-engineered from captured preamble sequences |
| Payload | 32 bytes (fixed) | BK2423 transmits fixed-length frames; shorter data is padded with idle fill |
| CRC | Disabled | BK2423's CRC algorithm differs from nRF24L01+'s; incompatible at hardware level |
| Auto-Ack | Disabled | One-way link (remote → receiver); no return channel needed |
| Dynamic Payload | Disabled | Fixed 32-byte frames simplify parsing |

### Hardware Address Filtering — The Pairing Mechanism

**Design Decision:** No software pairing protocol was implemented because the nRF24L01+ hardware address filter provides equivalent security for this use case.

The nRF24L01+ contains a hardware correlator that continuously scans the incoming bitstream for a valid preamble followed by the programmed 5-byte address. Only when all 40 address bits match does the chip begin clocking payload data into its FIFO. All other 2.4 GHz traffic is rejected at the analog/digital boundary.

**What this means in practice:**
- WiFi (2.4 GHz): Rejected — different modulation and framing
- Bluetooth: Rejected — different modulation, frequency hopping
- Other nRF24L01+ devices: Rejected — different address (probability of collision: 1 in 2^40 ≈ 1 in 1.1 trillion)
- Other BK2423 remotes: Rejected — different address (each remote has a factory-programmed address)
- Microwave ovens: Rejected — wideband noise doesn't correlate with address pattern

**Why not add software pairing on top?** Adding a challenge-response pairing protocol would provide zero additional security because:
1. The address already uniquely identifies this remote
2. There's no encryption (BK2423 doesn't support it at this data rate)
3. A determined attacker could replay captured packets regardless of pairing state
4. The threat model for cafe lights doesn't warrant cryptographic authentication

The "pairing" step in the software is simply confirming the remote is present and transmitting — a UX convenience, not a security measure.

### Background Noise Handling

During testing, a persistent background source was discovered sharing the same address `[38 72 2D A8 5E]` on channel 42. This source transmits ~30 packets/second with two known descrambled headers:

- **CMD packets:** First 4 bytes = `4C 6D 17 65`
- **IDLE packets:** First 4 bytes = `4C ED AD 0B`

**Why this happens:** The address space of 2^40 is large but not infinite. In a residential environment with multiple Enbrighten/Jasco products, address collision is possible. The BK2423's scrambling means the addresses look random on-air, making collisions a manufacturing lottery.

**Software solution:** A simple 4-byte header check filters these packets before dispatch. This is more robust than ignoring them at the radio level (which isn't possible — they pass legitimate address matching). The filter adds negligible CPU overhead (one 4-byte comparison per packet).

### Idle-Line Tail Detection

The BK2423 transmits fixed 32-byte frames regardless of actual payload length. When the remote's data is shorter than 32 bytes (which it always is — button codes are ~11-15 bytes), the remaining bytes are idle-line fill. On the raw (pre-scramble) wire, these appear as repeating patterns:

- `0xFF` (all ones — line idle HIGH)
- `0x55` (alternating bits — clock recovery pattern)
- `0xAA` (alternating bits, inverted)

The `find_idle_start()` function scans backward from byte 31, looking for these patterns with ±1 bit error tolerance (accounting for RF noise that may flip a single bit). The bytes before the idle tail are the meaningful button identifier.

**Why not just use a fixed payload length?** Different buttons may encode different amounts of data. The idle-tail detection adapts automatically without requiring prior knowledge of each button's payload length.

---

## Dual-Bus Hardware Architecture

### Why Two SPI Buses?

The Raspberry Pi 5 has multiple SPI peripherals. This project uses two of them for fundamentally different tasks that would conflict if shared:

| Requirement | Radio (SPI0) | LEDs (SPI1) |
|-------------|-------------|-------------|
| **Access Pattern** | Bursty: read FIFO when data available | Continuous: non-stop frame output |
| **Direction** | Bidirectional (read registers, write commands) | Unidirectional (write only) |
| **Timing Criticality** | Tolerant — can pause between transactions | Critical — gaps corrupt protocol |
| **Chip Select** | Manual GPIO toggle (nRF24L01+ requirement) | Kernel-managed (spidev) |
| **Transfer Size** | Small: 2-33 bytes per transaction | Large: 576 bytes per frame |
| **Frequency** | 1 MHz (nRF24L01+ max reliable) | 2 MHz (TM1815B timing requirement) |

**If we tried to share one bus:**
1. The LED refresh thread would need to release the bus every ~2ms for radio polls
2. The nRF24L01+'s manual CSN toggling would conflict with spidev's DMA engine
3. Bus contention would introduce unpredictable LED flicker
4. The radio might miss packets while waiting for a 576-byte LED transfer to complete

**By using separate buses:**
- Zero contention — both operate independently at full speed
- No scheduling complexity — each thread owns its bus exclusively
- Simpler error handling — a radio timeout can't affect LED output
- Hardware isolation — electrical faults on one bus can't corrupt the other

### SPI0: Radio Interface (Blinka/busio)

The nRF24L01+ requires a non-standard SPI interaction pattern:

1. Pull CSN (GPIO D8) LOW to begin transaction
2. Send command byte (register address or FIFO read command)
3. Clock data in/out simultaneously (full duplex)
4. Pull CSN HIGH to end transaction

Standard Linux spidev handles chip select automatically per `xfer2()` call, but the nRF24L01+ needs CSN held LOW across multi-byte sequences where the first byte is a command and subsequent bytes are data. The Adafruit Blinka library (busio/digitalio) provides the manual GPIO control needed.

**Why not use the circuitpython-nrf24l01 library?** Early testing revealed the library's abstraction layer was incorrectly configuring the radio for this specific BK2423 protocol (wrong address width, incompatible auto-ack settings). Raw register access provides exact control over every configuration bit, matching the settings proven to work in capture scripts.

### SPI1: LED Interface (spidev)

The TM1815B protocol is a unidirectional data stream — bytes go out, nothing comes back. Linux spidev with DMA is ideal here:

- **DMA transfers** ensure gap-free output at 2.0 MHz (the DMA engine feeds the SPI FIFO continuously, never starving it)
- **No chip select needed** — the LED data line has no select signal; it's always listening
- **Large transfers** (576 bytes) happen in a single `xfer2()` call with kernel-level DMA scheduling
- **No manual GPIO manipulation** required

**Why spidev instead of Blinka for LEDs?** Performance. Blinka's `busio.SPI.write()` does byte-by-byte transfers through Python, which would introduce inter-byte gaps at 2.0 MHz. spidev's `xfer2()` hands the entire buffer to the kernel's DMA engine, achieving true gap-free output.

### CE Pin (GPIO D25) — Receive Enable

The nRF24L01+'s CE (Chip Enable) pin controls the radio's operating mode:
- **CE HIGH + PRIM_RX=1**: Active RX mode — correlator running, packets being received
- **CE LOW**: Standby — radio powered but not receiving (saves ~12mA)

CE is held HIGH throughout normal operation (always listening). It's only pulled LOW during:
- Initial configuration (registers can only be written in standby)
- Power-down on shutdown

**Why not toggle CE between polls?** The nRF24L01+ takes 130µs to transition from standby to active RX. At a 1ms poll interval, we'd lose 13% of listening time to transitions. Keeping CE HIGH with the 3-packet FIFO means we never miss a packet even during the few microseconds of register reads.

### Level Shifter Placement

```
Pi GPIO 20 (3.3V) ──► Level Shifter ──► TM1815B Data-In (5V)
     │                      │                    │
     └── 50Ω trace ────────┘──── short trace ───┘
         (< 3cm)                  (< 5cm)
```

**Design Decision:** The level shifter is placed physically close to the Pi's GPIO header, not near the LED string.

**Rationale:** At 2.0 MHz, signal integrity degrades with trace length due to capacitive loading and reflections. The 3.3V signal from the Pi is clean but low-amplitude. By shifting to 5V close to the source, the higher-amplitude signal has better noise margin over the longer run to the LED string. A weak 3.3V signal over a long trace would be more susceptible to noise coupling from adjacent power lines.

---

## Non-Blocking State Machine Software Design

### Why Non-Blocking?

A naive implementation might use blocking calls:
```python
# BAD: Blocking design
while True:
    button = wait_for_button()      # Blocks until RF packet arrives
    handle_button(button)            # Might start an animation
    animation.run_until_stopped()    # Blocks until next button!
```

This fails because:
1. You can't receive a new button press while an animation is running
2. The LED refresh stops during blocking waits (causing flicker/blackout)
3. Timer expiry can't be processed during animations

**The solution: event-driven architecture with dedicated threads for independent concerns.**

### Thread Responsibilities

#### Main Thread — RF Polling + Button Dispatch

The main thread runs a tight poll loop at ~1000 Hz:

```python
while True:
    if radio.available():          # Non-blocking FIFO check
        raw = radio.read()          # 32-byte read (~300µs)
        desc = descramble(raw)      # In-memory transform (~50µs)
        # ... filter, debounce, dispatch
    time.sleep(0.001)              # Yield CPU for 1ms
```

**Why polling instead of interrupts?** The nRF24L01+'s IRQ pin is not connected in this hardware design. Even if it were, the Pi's GPIO interrupt latency (50-500µs depending on kernel scheduler load) offers no advantage over 1ms polling for a human-operated remote where button presses last 100-500ms.

**Why 1ms sleep?** Without the sleep, the loop would consume 100% of one CPU core spinning on SPI reads. The 1ms yield reduces CPU usage to <5% while still providing <2ms response time — imperceptible to humans (reaction time ≈ 200ms).

#### SPI Output Thread — Continuous LED Refresh

```python
def spi_loop():
    while state['running']:
        with lock:
            buf = state['buf']   # Grab pointer (fast)
        spi_led.xfer2(buf[:])    # Transmit frame (2.3ms)
```

This thread runs at maximum speed (~430 FPS), continuously pushing whatever frame buffer is current. It never sleeps, never blocks on I/O beyond the SPI transfer itself.

**Why continuous refresh?** The TM1815B LEDs require periodic data to maintain their output. If no data arrives for >280µs, the chip interprets silence as a reset and the LEDs may flicker or go dark. By continuously transmitting, we guarantee stable output regardless of what the main thread or animation thread is doing.

**Why copy the buffer pointer under lock?** The lock protects against the race condition where the animation thread is mid-update of `state['buf']` (assigning a new list) while the SPI thread reads it. The lock is held for nanoseconds (pointer copy), not milliseconds (SPI transfer).

#### Mode Thread — Animation Execution

Each animation (fade, strobe, chaser, theater, twinkle, preset) runs as a daemon thread that:
1. Builds new frame buffers on its own schedule
2. Updates `state['buf']` under the shared lock
3. Checks `mode_stop` Event to know when to exit

**Why a separate thread per animation instead of a state machine tick function?**

A tick-based approach would require:
```python
# Hypothetical tick-based design
def animation_tick():
    if mode == 'fade':
        fade_state.step += 1
        if fade_state.step >= fade_state.total_steps:
            fade_state.pick_new_target()
        buf = interpolate(fade_state)
```

This adds complexity:
- Every animation needs its own state struct
- The tick rate must be chosen to accommodate the fastest animation
- Adding a new animation requires integrating it into the tick dispatcher
- Testing requires simulating tick sequences

With threads, each animation is a self-contained function with local variables as state. The threading model lets each animation run at its natural pace (strobe: 1.25s intervals, twinkle: 30ms ticks) without a shared tick clock.

**Why `mode_stop.wait(timeout)` instead of `time.sleep()`?**

```python
# BAD: Can't interrupt for up to 1.25 seconds
time.sleep(STROBE_INTERVAL)

# GOOD: Returns immediately if mode_stop is set
if mode_stop.wait(STROBE_INTERVAL):
    return  # Clean exit in <1ms
```

The `Event.wait(timeout)` call blocks for up to `timeout` seconds but returns immediately (True) if the event is set from another thread. This means pressing any button while an animation is running terminates it within one tick — worst case 30ms for twinkle (the fastest tick), instant for strobe/chaser/theater.

#### Timer Thread — Auto Power-Off

```python
def _wait():
    if not timer_cancel.wait(hours * 3600):
        timer_fire()  # Timer expired naturally
    # else: timer was cancelled, do nothing
```

**Why a thread instead of checking elapsed time in the main loop?**

Checking `time.monotonic() - timer_start > duration` in the main loop would work but adds conditional logic to every iteration of a 1000 Hz loop. A sleeping thread costs zero CPU until it wakes. The `Event.wait()` approach also provides clean cancellation without race conditions.

### State Management

All mutable state lives as local variables in `main()`, captured by nested function closures:

```python
def main():
    dimmer_idx = 9
    current_wrgb = None
    active_mode = None
    # ... etc

    def handle_button(btn_name):
        nonlocal dimmer_idx, current_wrgb, active_mode
        # Direct access to state, no object indirection
```

**Why closures instead of a class?**

A `ControllerState` class would work equally well. Closures were chosen because:
1. The state is only accessed from within `main()` — no external consumers
2. `nonlocal` declarations make mutation explicit (you can see at a glance which functions modify which state)
3. No `self.` prefix noise on every access
4. The threading primitives (lock, mode_stop, timer_cancel) are naturally in scope

### Debounce Strategy

The remote sends repeated packets while a button is held (the membrane keypad shorts continuously, causing the BK2423 to retransmit). Without debouncing, a single press might register 5-20 times.

```python
if code == last_button_code and (now - last_button_time) < DEBOUNCE_S:
    continue  # Same button within 300ms — ignore
last_button_code = code
last_button_time = now
```

**Why 300ms?** Human button press duration is typically 100-300ms for a "tap" and 300ms+ for a "hold." At 300ms debounce:
- A single tap registers once (correct)
- Rapid double-taps (>300ms apart) register twice (correct)
- Holding the button for 2 seconds registers once (correct — we want single-action per press)

**Why per-code debouncing instead of per-button-name?** The code is available before the button name lookup. If the code doesn't match any known button, we still debounce it (avoiding log spam from unknown packets). This also handles the edge case where two buttons might produce the same hex code due to RF corruption — they'd be debounced as one event.

### Power Toggle State Preservation

When Power is pressed to turn off:
```python
saved_setting = {
    'wrgb': current_wrgb,       # What color was showing
    'mode': active_mode,         # What animation was running
    'dimmer_idx': dimmer_idx,    # Brightness level
    'modes_idx': modes_idx,      # Position in Modes cycle
    'dual_color1': dual_color1,  # Dual-color slot 1
    'dual_color2': dual_color2,  # Dual-color slot 2
}
```

When Power is pressed to turn on, this snapshot is restored — the animation resumes, the color returns, the dimmer stays where it was. This matches user expectations: "power off" means "pause and go dark," not "factory reset."

---

## Logic Level Optimization

### The Voltage Problem

The TM1815B LED driver IC specifies:
- **V_IH (input HIGH minimum):** ~3.5V (70% of VDD at 5V)
- **V_IL (input LOW maximum):** ~1.5V (30% of VDD at 5V)

The Raspberry Pi 5's GPIO outputs:
- **Logic HIGH:** 3.3V
- **Logic LOW:** 0V

The Pi's 3.3V HIGH is **below** the LED's 3.5V threshold. Direct connection results in the LEDs interpreting HIGH as indeterminate — sometimes recognized, sometimes not, causing data corruption and random LED behavior.

### Level Shifting

A unidirectional 3.3V→5V level shifter translates voltage domains:

```
Pi GPIO 20          Level Shifter         TM1815B Data-In
   3.3V logic  ──►  Input: 3.3V ref  ──►  5V logic
                     Output: 5V ref
   0V (LOW)    ──►  0V (pass-through) ──►  0V (valid LOW ✓)
   3.3V (HIGH) ──►  5V (shifted)      ──►  5V (valid HIGH ✓)
```

**Why unidirectional?** The TM1815B data line is write-only. There's no data flowing back from LEDs to Pi. A bidirectional shifter would work but adds unnecessary complexity and cost.

**Shifter type:** A simple MOSFET-based level shifter (BSS138) provides adequate rise/fall times at 2 MHz. The 4 MHz bandwidth of common BSS138 shifters gives comfortable margin above the 2 MHz SPI clock.

### SPI Clock Rate: Why 2.0 MHz Specifically

The TM1815B protocol encodes data as pulse widths:

| Data Bit | LOW Duration | HIGH Duration | Bit Period |
|----------|-------------|---------------|------------|
| Logic 1 | 1300-2000 ns | 500-1000 ns | ~2000 ns |
| Logic 0 | 620-820 ns | 1500-2000 ns | ~2000 ns |
| Reset | — | >280 µs continuous HIGH | — |

The "natural" data rate is 500 kHz (2µs per bit). To encode this via SPI, each data bit is represented by multiple SPI bits. The ratio of SPI clock to data rate determines encoding resolution.

**Why not 1.6 MHz (exact 4× data rate)?**

Testing on the Raspberry Pi 5 revealed that at 1.6 MHz, the SPI peripheral inserts **inter-byte gaps** — brief pauses between consecutive bytes where the clock stops. This is a hardware artifact of the Pi 5's SPI DMA controller hitting a non-optimal clock divider ratio.

These gaps appear as unexpected HIGH periods in the middle of a data frame. Since HIGH is the idle/reset state, gaps >280ns can trigger a partial reset, corrupting the frame.

**Why 2.0 MHz works:**

At 2.0 MHz, the Pi 5's clock divider produces a clean integer ratio from the peripheral clock. The DMA engine feeds bytes continuously with zero inter-byte gaps — verified via oscilloscope capture. The 4-bit encoding scheme was designed around this specific clock rate.

**Trade-off:** At 2.0 MHz, each SPI bit is 500ns. With 4 SPI bits per data bit, the actual pulse widths are:
- Logic 1: LOW = 3 × 500ns = 1500ns ✓ (within 1300-2000 spec)
- Logic 0: LOW = 1 × 500ns = 500ns — technically at the LOW edge of the 620-820ns spec

In practice, the TM1815B accepts this. The chip's internal clock recovery has significant tolerance, and the 500ns LOW for logic-0 is reliably detected because:
1. The transition edge (HIGH→LOW) is what the chip actually clocks on
2. The minimum LOW time spec (620ns) assumes worst-case clock recovery; the actual silicon accepts shorter pulses
3. Extensive testing across temperature ranges confirms reliable operation

### The 4-Bit Encoding Scheme

Each data bit is encoded as 4 SPI bits:

```
Data Bit = 1:  SPI outputs 0b0001
                           ───┐         ┌──
                              │         │    SPI line idles HIGH (0xFF)
                              └─────────┘    so 0=LOW, 1=HIGH
               Duration:  LOW 1500ns │ HIGH 500ns

Data Bit = 0:  SPI outputs 0b0111
                           ───┐   ┌──────
                              │   │
                              └───┘
               Duration:  LOW 500ns │ HIGH 1500ns
```

**Why 4 bits and not 3 or 5?**

- **3 bits (667ns per SPI bit at 1.5 MHz):** Doesn't hit a clean clock divider; inter-byte gaps appear
- **4 bits (500ns per SPI bit at 2.0 MHz):** Clean divider, gap-free DMA, pulse widths within spec
- **5 bits (400ns per SPI bit at 2.5 MHz):** Would require 2.5 MHz SPI clock; level shifter bandwidth becomes marginal; wastes 25% more bus bandwidth

**Encoding math:** One color byte (8 data bits) × 4 SPI bits = 32 SPI bits = 4 SPI bytes. One WRGB pixel = 4 color bytes × 4 SPI bytes = 16 SPI bytes.

### Pre-Computed Lookup Table

The encoding from data byte to 4 SPI bytes is a fixed transformation. Computing it at runtime for every byte of every frame would be wasteful:

```
Frames per second: ~430
Bytes per frame to encode: 6 LEDs × 4 colors + C1(4) + C2(4) = 32 bytes
Encode operations per second: 430 × 32 = 13,760
```

While not enormous, each encode involves an 8-iteration loop with bit shifting. The LUT eliminates this entirely:

```python
LUT_4BIT = [encode_byte_4bit(v) for v in range(256)]  # Built once at startup

# Frame construction becomes simple concatenation:
for w, r, g, b in pixels:
    buf += LUT_4BIT[w] + LUT_4BIT[r] + LUT_4BIT[g] + LUT_4BIT[b]
```

**Memory cost:** 256 entries × 4 bytes = 1,024 bytes. Negligible.
**Speed benefit:** Frame construction drops from ~200µs to ~50µs (Python bytecode: LUT index + bytes concatenation vs. 8-iteration bit loop per byte).

### Frame Structure

```
│◄────── RESET ──────►│◄─ C1 ──►│◄─ C2 ──►│◄─── D1 ───►│◄─── D2 ───►│...│◄────── RESET ──────►│
│     80 × 0xFF       │  4 × 4B  │  4 × 4B  │  4 × 4 × 4B │  4 × 4 × 4B │   │     80 × 0xFF       │
│     (40 µs HIGH)    │  (16B)   │  (16B)   │   (64B)     │   (64B)     │   │     (40 µs HIGH)    │
│     Latch/Reset     │  Current │  ~Current │  LED 1 WRGB │  LED 2 WRGB │   │     Latch/Reset     │
```

**Frame size for 6 LEDs:** 80 + 16 + 16 + (64 × 6) + 80 = **576 bytes**

**Transfer time:** 576 bytes × 8 bits ÷ 2,000,000 Hz = **2.304 ms**

**Maximum refresh rate:** 1000 ÷ 2.304 = **~434 FPS**

This is far above the human flicker perception threshold (~60 Hz) and well above LED PWM frequencies (~1 kHz internal to TM1815B). The continuous refresh ensures the LEDs never enter reset state accidentally.

### C1/C2 Current-Setting Registers

The TM1815B uses the first two "pixels" in each frame as configuration registers:

- **C1 = [0x20, 0x20, 0x20, 0x20]:** Sets maximum current per channel (W, R, G, B). 0x20 = 32/63 of max (~50% of 30mA = ~15mA per channel).
- **C2 = [0xDF, 0xDF, 0xDF, 0xDF]:** Bitwise complement of C1 (error detection). If C2 ≠ ~C1, the frame is rejected.

**Why 50% current (0x20) instead of full (0x3F)?**

1. **LED longevity:** Running at 50% max current significantly extends LED lifespan
2. **Heat management:** Lower current = less heat in the PCB-mounted LEDs
3. **Dimmer range:** Software dimming (0-255 PWM values) applied on top of the hardware current limit gives effective 8-bit control within a safe operating range
4. **Power supply headroom:** 6 LEDs × 4 channels × 15mA = 360mA total vs. 720mA at full current

### Continuous Refresh vs. Single-Shot

Some LED protocols (WS2812B) latch data on silence and hold it indefinitely. The TM1815B is different — it expects continuous data refresh and may exhibit drift or reset behavior if data stops flowing.

**Design Decision:** The SPI output thread runs without sleep, continuously re-transmitting the current frame buffer.

**Trade-offs:**
- **Pro:** Rock-solid output regardless of software timing; no flicker during mode transitions
- **Pro:** Frame buffer updates appear on the LEDs within 2.3ms (one frame time)
- **Con:** Uses one CPU core at ~30% utilization (mostly kernel DMA, minimal Python)
- **Con:** SPI1 bus is always busy (not shared with other peripherals)

For a dedicated LED controller, these trade-offs are acceptable. The Pi 5 has 4 cores; dedicating partial usage of one to LED output is well within budget.

---

## Design Decisions & Rationale

### Why Raw Register Access Instead of a Library?

The project uses direct nRF24L01+ register writes (`_reg_write(0x00, 0x03)`) instead of a high-level library like `circuitpython-nrf24l01`.

**Reason:** The BK2423 compatibility mode requires exact register values that the library either doesn't expose or sets incorrectly:
- CRC must be disabled (library defaults to CRC-16)
- Auto-ack must be disabled on all pipes (library enables it by default)
- Dynamic payload must be off (library may enable features register)
- The address must be written MSB-first in a specific pipe configuration

Early attempts with the library resulted in either no packets received or corrupted data. Raw register access gave immediate success because the working configuration was copied directly from proven capture scripts.

### Why Python Instead of C?

**For the LED driver:** Python with spidev achieves gap-free 2.0 MHz output because the actual SPI transmission is handled by the kernel's DMA engine (written in C). Python only builds the frame buffer and hands it off. The buffer construction (~50µs with LUT) is negligible compared to the 2.3ms DMA transfer.

**For the RF receiver:** At a 1ms poll interval, the Python overhead for descrambling 32 bytes (~50µs) and one dict lookup (~1µs) is insignificant. We're nowhere near the timing budget limit.

**For animations:** The slowest animation tick is 30ms (twinkle). Building a 576-byte frame buffer takes ~50µs in Python. Even with GIL scheduling, there's >29ms of headroom per tick.

**If Python weren't fast enough:** The only path to failure would be if frame buffer construction took >2.3ms (one frame time), causing the SPI thread to starve. With the LUT approach, construction takes ~50µs — 46× safety margin.

### Why Threading Instead of asyncio?

The `spidev.xfer2()` call is a blocking kernel syscall (DMA transfer). asyncio can't yield during a blocking syscall without wrapping it in `run_in_executor()`. Since the SPI output thread is a tight loop of blocking calls, threading is the natural fit.

Additionally, `busio.SPI` (Blinka) is not async-compatible. The lock-based approach with threads is simpler and more predictable than mixing async I/O with synchronous hardware libraries.

### Why Store Button Codes in JSON?

Button hex codes are stored in `button_codes.json` rather than hardcoded because:

1. **Not yet captured:** The RF button codes require physical testing with the remote in a low-noise environment. They can't be known at development time.
2. **May vary between remotes:** Different Jasco remotes likely have different factory-programmed codes.
3. **User-updatable:** Running `--learn` mode re-captures all codes without code changes.
4. **Debuggable:** JSON is human-readable — you can inspect/edit it to fix misdetections.

### Why All-in-One main.py Initially?

The first implementation put everything in one file because:
1. The system was being built iteratively (RF protocol wasn't fully understood)
2. The closure-based state management is simpler than passing state objects between modules
3. A single file is easier to deploy to the Pi (one `scp` command)

The modular split (led_driver.py, remote_button_map.py, rf_pair.py, rf_receiver.py) improves maintainability now that the architecture is stable.

---

## Summary

This system demonstrates that consumer RF protocols can be intercepted and driven by commodity hardware (Raspberry Pi + nRF24L01+) with minimal additional circuitry (one level shifter). The key insights are:

1. **BK2423/nRF24L01+ compatibility** makes interception possible without an SDR or custom radio hardware
2. **Hardware address filtering** provides sufficient device isolation without a software pairing protocol
3. **Dual independent SPI buses** eliminate hardware contention between unrelated subsystems
4. **Event-based threading** gives sub-millisecond response time with minimal CPU usage
5. **SPI bit-banging with DMA** achieves precise LED timing without real-time kernel patches
6. **Pre-computed lookup tables** move the encoding cost from runtime to startup

The total bill of materials added to a Raspberry Pi 5 is: one nRF24L01+ module (~$2), one BSS138 level shifter (~$0.50), and hookup wire. The software handles the rest.
