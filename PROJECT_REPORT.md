# RF Signal Interception and LED Control on the Raspberry Pi 5: A Custom Controller for Enbrighten RGBW Cafe Lights

---

## Abstract

This report presents the design and implementation of a custom LED controller built on the Raspberry Pi 5 that intercepts radio frequency signals from a commercial Jasco QOBRGBXYZA 25-button remote control and drives an Enbrighten TM1815B RGBW LED cafe light string. The system replaces the stock controller IC with a software-defined solution, preserving the original user experience while adding features impossible with the factory hardware — including custom animation algorithms, 10-step dimming, dual-color modes, and programmable auto-off timers. This report discusses the RF interception strategy, dual-bus hardware architecture, non-blocking software state machine, logic level optimization, and the implementation details of every major code subsystem.

---

## 1. Introduction and Motivation

The Enbrighten LED cafe light string ships with a basic controller that accepts commands from its paired RF remote. The stock controller offers limited functionality: a small color palette, a single white temperature, minimal animations, and coarse 4-step dimming. By intercepting the remote's RF signal at the receiver side and driving the LED string directly from a Raspberry Pi 5, the project gains complete software-defined control over every aspect of light output.

The design philosophy is transparent replacement — the user presses the same physical buttons on the same remote, and the lights respond. No mobile app, no WiFi dependency, no cloud service. The remote's 25 buttons map to a richer feature set than the original controller ever offered, and new features can be deployed via a simple software update without touching hardware.

The remote uses an XNS1042 transmitter IC implementing the XN297L protocol, a Chinese-manufactured RF transceiver that is wire-compatible with Nordic Semiconductor's nRF24L01+ at the physical layer. This compatibility is the key insight that makes the entire project feasible: a commodity nRF24L01+ receiver module (under two dollars) can hear the remote's transmissions directly, provided the payload is descrambled in software. The receiver module connects to the Pi's SPI bus, while the LED data line uses a separate SPI bus with a voltage level shifter to meet the TM1815B's 5V logic requirement. The stock controller board uses a PIC12F683 microcontroller as the decoder IC, which this project replaces entirely with the Raspberry Pi 5.

---

## 2. RF Signal Interception

### 2.1 The XNS1042 / XN297L Protocol

The XN297L protocol family was designed as a cheaper drop-in replacement for the nRF24L01+ in consumer products. The XNS1042 IC in the Jasco remote implements this protocol, maintaining identical modulation (GFSK at 1 Mbps), preamble structure, and address matching behavior, which means an nRF24L01+ receiver recognizes XN297L packets as valid. However, the XN297L adds two encoding steps to the payload before transmission: bit reversal (each byte's MSB and LSB are swapped) and XOR scrambling with a fixed 32-byte table known as Scramble Table B. These transformations provide minimal over-the-air obfuscation without requiring any firmware awareness — the XNS1042 handles scrambling transparently in hardware.

For interception, the nRF24L01+ receiver is configured for channel 42 (2442 MHz) with a 5-byte address of [0x38, 0x72, 0x2D, 0xA8, 0x5E], 1 Mbps data rate, no CRC, no auto-acknowledgment, and a fixed 32-byte payload width. CRC is disabled because the XN297L's CRC algorithm is incompatible with the nRF24L01+'s implementation — enabling it would cause every valid packet to fail the check. Auto-acknowledgment is disabled because the remote is a one-way transmitter; it never listens for responses.

The descramble process in software is straightforward: for each received byte at position i, the code looks up its bit-reversed value in a pre-computed 256-entry table, then XORs the result with SCRAMBLE_B[5 + i] (the scramble table offset by the 5-byte address width). This transforms the raw on-air bytes back into the original payload the remote intended to send.

### 2.2 Hardware Address Filtering as Pairing

The nRF24L01+ contains a hardware correlator that continuously scans the incoming 2.4 GHz bitstream for a valid preamble followed by the programmed 5-byte address. Only when all 40 address bits match does the chip clock payload data into its receive FIFO. All other 2.4 GHz traffic — WiFi, Bluetooth, other nRF24L01+ devices, microwave ovens — is rejected at the hardware level with a collision probability of approximately one in 1.1 trillion.

No software pairing protocol was implemented on top of this hardware filter because it would provide zero additional security. The address already uniquely identifies this specific remote, the XN297L provides no encryption at this data rate, and the threat model for decorative cafe lights does not warrant cryptographic authentication. The "pairing" step in the software is simply a confirmation that the remote is present and actively transmitting — a user experience convenience, not a security measure. The code waits for two non-background packets to arrive within five seconds of each other, confirming an active transmitter.

### 2.3 Background Noise and Idle-Line Detection

During development, a persistent background source was discovered sharing the same RF address on channel 42, transmitting approximately 30 packets per second with two identifiable header patterns. This likely originates from another Jasco product in the environment — the scrambled address space makes such collisions a manufacturing lottery. The software filters these packets with a simple 4-byte header comparison before dispatch, adding negligible CPU overhead.

Additionally, the XN297L transmits fixed 32-byte frames regardless of actual data length. Button codes occupy only 11–15 bytes; the remaining bytes are idle-line fill that appears as repeating 0xFF, 0x55, or 0xAA patterns in the raw domain. The idle-tail detection algorithm scans backward from byte 31 to find where meaningful data ends, with single-bit error tolerance to account for RF noise.

---

## 3. Dual-Bus Hardware Architecture

### 3.1 Design Rationale

The system uses two independent SPI buses for fundamentally different tasks. SPI0 handles the radio: bursty bidirectional transfers of 2–33 bytes with manual chip-select toggling at 1 MHz. SPI1 handles the LEDs: continuous unidirectional transmission of 576-byte frames via DMA at 2 MHz. These access patterns are incompatible on a shared bus — the LED refresh thread would need to release the bus every two milliseconds for radio polls, the nRF24L01+'s manual CSN toggling would conflict with spidev's DMA engine, and the radio might miss packets while waiting for a large LED transfer to complete.

By dedicating separate buses, both peripherals operate independently at full speed with zero scheduling complexity. Each thread owns its bus exclusively, and electrical faults on one bus cannot corrupt the other.

### 3.2 SPI0: Radio Interface

The nRF24L01+ requires a non-standard SPI transaction pattern: pull CSN low, send a command byte, transfer data bytes (simultaneously reading the response), then release CSN high. The Adafruit Blinka library (busio/digitalio) provides the manual GPIO control needed for this pattern. The higher-level circuitpython-nrf24l01 library was evaluated but rejected because it incorrectly configured the radio for XN297L compatibility — wrong CRC settings, auto-ack enabled by default, and dynamic payload behavior that caused interoperability failures. Raw register access gave immediate success because the working configuration was copied directly from proven RF capture scripts.

### 3.3 SPI1: LED Interface

The TM1815B protocol requires gap-free byte streams — any pause exceeding 280 nanoseconds triggers a reset condition. Linux spidev with kernel DMA is ideal because the DMA engine feeds the SPI FIFO continuously without CPU intervention, producing true gap-free output. The entire 576-byte frame is handed off in a single xfer2() call. Blinka's busio.SPI was measured to introduce inter-byte gaps at high speeds due to byte-by-byte Python-level transfers, making it unsuitable for LED output.

---

## 4. Logic Level Optimization

### 4.1 The Voltage Gap

The TM1815B specifies a minimum input HIGH threshold of approximately 3.5V (70% of its 5V supply). The Raspberry Pi 5's GPIO outputs only 3.3V for logic HIGH — below the LED's threshold. Direct connection results in unreliable data recognition, causing random LED behavior. A unidirectional MOSFET-based level shifter (BSS138) translates the Pi's 3.3V signals to 5V, placed physically close to the Pi's GPIO header so the higher-amplitude signal has better noise margin over the longer trace run to the LED string.

### 4.2 SPI Clock Rate Selection

The TM1815B's natural data rate of 400 kHz suggests an SPI clock of 1.6 MHz (4× oversampling). However, testing revealed that the Pi 5's SPI peripheral inserts inter-byte gaps at 1.6 MHz due to a non-optimal clock divider ratio. These gaps corrupt the protocol by introducing unexpected reset conditions mid-frame. At 2.0 MHz, the clock divider produces a clean integer ratio (250 MHz core / 125 = 2.0 MHz), and the DMA engine achieves verified gap-free output. The 4-bit encoding maps each data bit to four SPI bits: Logic 1 becomes 0b0001 (1500 ns LOW, 500 ns HIGH) and Logic 0 becomes 0b0111 (500 ns LOW, 1500 ns HIGH). A pre-computed 256-entry lookup table maps every possible byte value to its 4-byte SPI encoding at startup, reducing per-frame construction from approximately 200 microseconds to 50 microseconds.

---

## 5. Non-Blocking Software State Machine

### 5.1 Threading Model

A naive blocking implementation would fail because receiving a button press during an animation, refreshing LEDs during a blocking wait, and processing timer expiry during animations are all operations that must occur concurrently. The solution is an event-driven architecture with four dedicated threads.

The main thread runs the RF receive loop at approximately 1000 Hz, polling the radio's FIFO, descrambling packets, filtering background noise, debouncing, and dispatching to the button handler. The one-millisecond sleep between polls reduces CPU usage to under five percent while providing sub-two-millisecond response time — imperceptible to humans whose reaction time is approximately 200 milliseconds.

The SPI output thread continuously pushes the current frame buffer to the LED string at approximately 430 frames per second. It never sleeps; the 2.3-millisecond DMA transfer itself provides natural pacing. This ensures the TM1815B LEDs never enter an inadvertent reset state regardless of what the other threads are doing.

The mode thread runs the active animation (fade, strobe, chaser, theater chase, twinkle, or preset pattern). Each animation is a self-contained function with local variables as its state, running at its natural pace — strobe at 1.25-second intervals, twinkle at 30-millisecond ticks. The critical design pattern is using Event.wait(timeout) instead of time.sleep(): the wait returns immediately when the stop event is set from another thread, giving sub-millisecond animation termination without polling overhead.

The timer thread implements 2-hour and 4-hour auto-off functionality using the same event-wait pattern for clean cancellation.

### 5.2 State Management and Button Dispatch

All mutable controller state lives as closure variables inside the main() function: the current color, active animation mode, dimmer level, white temperature index, dual-color slots, power state, and saved settings for power restoration. The handle_button() function accesses these via nonlocal declarations, making state mutations explicit and visible.

The button dispatch handles 25 buttons with distinct behaviors. The Power button toggles with full state save and restore — turning off captures a snapshot of the current color, animation, dimmer level, and dual-color configuration; turning on restores exactly that state, including restarting animations. Color buttons have dual behavior depending on whether a pending Color1/Color2 slot selection is active. The Modes button cycles through seven entries (four holiday presets and three animation types). The Dimming button cycles ten brightness levels but is intelligently ignored during animations that don't support dimming.

The debounce strategy uses a 300-millisecond window per unique hex code. The remote sends repeated packets while a button is held; debouncing ensures one action per press while allowing rapid sequential presses of different buttons.

### 5.3 Animation Implementation

Six distinct animations demonstrate the architecture's flexibility. Fade smoothly crossfades between random colors using linear interpolation over 50 steps per 1.5-second transition. Strobe performs hard cuts to random colors at 1.25-second intervals. Chaser shifts colors through the LED chain like a marquee, respecting the current dimmer level in real time. Theater Chase cycles a classic every-third-LED pattern. Twinkle independently fades each LED through pre-generated color sequences with staggered delays to create an asynchronous twinkling effect — a per-LED state machine with four phases (delay, fade-down, fade-up, hold) running at 33 frames per second. Static presets display repeating holiday color patterns across the six-LED string.

---

## 6. Conclusion

This project demonstrates that consumer RF protocols can be intercepted and driven by commodity hardware with minimal additional circuitry. The key enabling factors are XN297L/nRF24L01+ radio compatibility, hardware address filtering as a sufficient pairing mechanism, dual independent SPI buses eliminating hardware contention, event-based threading providing sub-millisecond response times with minimal CPU usage, SPI bit-banging with kernel DMA achieving precise LED timing without real-time kernel patches, and pre-computed lookup tables moving encoding costs from runtime to startup.

The entire system runs from a single Python file with no external dependencies beyond standard Linux SPI and GPIO libraries. Python was chosen over C because the actual timing-critical operations (SPI DMA transfers at 2.0 MHz, radio register accesses at 1 MHz) are handled by kernel drivers written in C — Python merely builds frame buffers and dispatches button events, tasks where its approximately 50-microsecond overhead is negligible against millisecond-scale hardware operations.

The architecture scales naturally to additional features: more LEDs (increase NUM_LEDS), new animations (add a loop function and register it in start_mode), additional remote buttons (run --learn mode), or entirely different LED protocols (swap the encoding LUT and frame builder). The modular design within the single file — pure data definitions at top, stateless encoding functions in the middle, stateful controller logic at bottom — enables confident modification without fear of breaking unrelated functionality.

---

*Total system: 1,385 lines of Python, one JSON configuration file, zero external Python package dependencies beyond Adafruit Blinka and spidev.*
