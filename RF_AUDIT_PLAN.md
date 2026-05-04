# RF Protocol Re-Audit Plan

Complete step-by-step procedure to re-verify the Jasco QOBRGBXYZA remote
RF protocol from scratch. Each step builds on the previous one.

---

## Step 0: Preparation

**Physical setup:**
- Ensure 10µF cap is on nRF24L01+ VCC/GND
- Note the distance between remote and antenna (keep it consistent, ~1 meter)
- Have the remote's batteries fresh
- Know where the LED controller (stock Enbrighten receiver) is

**Before every script:** Run `python3 rf_scanner_reset.py` to clear radio state.

---

## Step 1: Identify the Idle Signal Source

**Question:** Is the constant `4CEDAD0B...` signal coming from the remote,
the LED controller, or something else?

**Procedure:**
1. Power OFF the Enbrighten LED controller (unplug it completely)
2. Run: `python3 rf_scanner_reset.py && python3 rf_promisc_scan.py`
3. Do NOT press any remote buttons. Wait 30 seconds.
4. Record the packet rate (from heartbeat lines)
5. Now power the LED controller back ON (plug it in)
6. Wait 30 seconds, record packet rate again
7. Ctrl+C, save results

**Pass criteria:**
- If packet rate CHANGES when LED controller powers on → LED controller is transmitting
- If packet rate stays the SAME → idle signal comes from elsewhere (remote or environment)

**Also test:** Remove batteries from the remote and repeat. If packets stop,
the remote transmits idle. If packets continue with no remote and no LED
controller, it's environmental noise.

**Save results as:** `audit_step1_idle_source.txt`

---

## Step 2: Verify Channel

**Question:** Is channel 42 correct? Are there other active channels?

**Script needed:** `rf_channel_scan.py` (NEW — needs to be created)

This script should:
- Scan all 126 channels (0-125)
- Dwell on each channel for 200ms
- Count packets received per channel
- Run two passes: one with remote idle, one while holding a button
- Report which channels have elevated packet counts during button press

**Procedure:**
1. Run the scanner with remote idle (no buttons) → establishes noise per channel
2. Run again while HOLDING the Power button the entire time
3. Compare: channels with significantly more packets during button hold are
   candidates

**Pass criteria:**
- Channel 42 should show a clear spike during button press
- If OTHER channels also spike, the remote may use multiple channels
- If channel 42 does NOT spike, our channel assumption is wrong

**Save results as:** `audit_step2_channel_scan.txt`

---

## Step 3: Verify Data Rate

**Question:** Is 1 Mbps correct? Could it be 250 kbps or 2 Mbps?

**Script needed:** `rf_rate_test.py` (NEW — needs to be created)

This script should:
- Listen on channel 42 at each of the three data rates: 250 kbps, 1 Mbps, 2 Mbps
- For each rate: capture for 10 seconds while holding Power button
- Count total packets and command-pattern packets at each rate

**Procedure:**
1. Hold Power button continuously during each 10-second capture window
2. Script auto-cycles through the three rates

**Pass criteria:**
- The correct rate will have the MOST packets with structured (non-0xFF) content
- Wrong rates will produce mostly garbage or zero packets
- 1 Mbps should win if previous findings are correct

**Save results as:** `audit_step3_data_rate.txt`

---

## Step 4: Verify Address and Scramble Table

**Question:** Is [0x38, 0x72, 0x2D, 0xA8, 0x5E] with Scramble Table B
correct?

**Script:** `python3 rf_bk2423_scanner.py` (existing)

**Procedure:**
1. Hold the POWER button for the entire duration (~3 minutes)
2. The scanner tests all combinations automatically
3. Save the full output

**Pass criteria (same as before, but verify independently):**
- SCR_B+BREV [FF]*5 (= address [38 72 2D A8 5E]) should produce:
  - More structured packets than any other combination
  - Descrambled samples starting with `4C6D1765` (command) or `4CEDAD0B` (idle)
  - Highest "consistent positions" count among scrambled variants
- SCR_A variants should produce fewer or less-structured packets
- Raw (no scramble) should produce many packets but all 0xFF garbage

**Save results as:** `audit_step4_address_verify.txt`

---

## Step 5: Extended Command Capture — All 25 Buttons

**Question:** Can we get 10+ clean command packets for every button?

**Script:** `python3 rf_tap_capture.py` (existing, but may need longer
capture window)

The previous run got 0 command packets for Violet, Green_Prime, and
White_Select. This needs to be fixed.

**Procedure:**
1. For each button: TAP rapidly for 30 seconds (not 20)
2. Aim for quick press-release cycles, ~2 taps per second
3. Pay special attention to the buttons that previously failed:
   - Violet, Green_Prime, White_Select: try 60 seconds each
   - Make sure you're pressing the correct physical button
4. Keep the remote within 0.5 meters of the antenna

**Pass criteria:**
- Every button should have at least 5 command packets (starting with `4C6D1765`)
- If a button consistently produces 0 commands after 60 seconds of tapping,
  it may use a different protocol or channel

**Save results as:** `audit_step5_all_buttons.txt`

---

## Step 6: Byte-Level Analysis

**Question:** Which bytes are truly constant, which vary, and is there any
per-button pattern?

**Script needed:** `rf_byte_analysis.py` (NEW — needs to be created)

This script should take the Step 5 results and produce:
- For each byte position (0-31): list of all values seen across ALL buttons
- Per-button: which bytes are constant within that button
- Cross-button: which bytes differ between buttons consistently
- Hamming distance between each pair of packets from the SAME button
- Hamming distance between packets from DIFFERENT buttons
- Check bytes 28-31 specifically: could they be a CRC?

**Analysis questions:**
1. Bytes 0-3 (`4C6D1765`): Are these truly fixed or do they have any variation?
2. Bytes 4-7: How much variation? Is it bit errors or real data?
3. Bytes 8-13: Same question
4. Byte 14: Previous analysis showed bits 0-2 always `111` — confirm with
   larger sample
5. Bytes 14-31: Is there ANY per-button clustering? (Even if weak)
6. Bytes 28-31: Calculate CRC-16 and CRC-8 over bytes 0-27 and see if
   bytes 28+ match any standard CRC polynomial

**Pass criteria:**
- With 10+ packets per button, we can statistically distinguish "bit errors"
  (rare, random position) from "protocol fields" (specific positions change)
- If per-button clustering exists in any bytes, we have a path to button ID
- If CRC is found, we can filter corrupted packets reliably

**Save results as:** `audit_step6_byte_analysis.txt`

---

## Step 7: Idle vs Command Packet Structure

**Question:** What exactly distinguishes idle from command packets?

**Script needed:** Part of `rf_byte_analysis.py` or separate script

**Procedure:**
1. Capture 30 seconds with NO button presses → all idle packets
2. Capture 30 seconds while tapping Power → mix of idle + command
3. Compare the two sets

**Analysis:**
- How stable is the idle pattern? Is it exactly `4CEDAD0B8725766296BE7EEE9CE79A6831654ECD` every time or does it vary?
- Do idle packets appear DURING a button press, or does the remote switch
  entirely to command mode while transmitting?
- How quickly after a button release does the idle pattern resume?

**Pass criteria:**
- Clear understanding of idle packet structure and stability
- Timing relationship between command and idle packets documented

**Save results as:** `audit_step7_idle_vs_command.txt`

---

## Summary: What Confirms the Protocol

The RF protocol is "accurately determined" when ALL of these are true:

1. **Channel**: Step 2 shows channel 42 has a clear packet spike during button
   press, and no other channel shows the same
2. **Data rate**: Step 3 shows 1 Mbps produces structured data, other rates
   do not
3. **Address + scramble**: Step 4 reproduces the original scanner result —
   SCR_B+BREV with [FF]*5 is the clear winner
4. **Command detection**: Step 5 captures 5+ command packets for ALL 25
   buttons using the `4C6D1765` header filter
5. **Packet structure**: Step 6 provides a clear byte map showing which
   positions are fixed protocol, which are variable, and whether CRC exists
6. **Idle source**: Step 1 identifies what's transmitting the constant signal

## Scripts to Create

| Script | Purpose | For Step |
|--------|---------|----------|
| `rf_channel_scan.py` | Scan all 126 channels with/without button press | Step 2 |
| `rf_rate_test.py` | Test 250k/1M/2M data rates on ch 42 | Step 3 |
| `rf_byte_analysis.py` | Statistical byte-level analysis of captures | Step 6 |

Existing scripts to reuse:
- `rf_scanner_reset.py` (before every test)
- `rf_promisc_scan.py` (Step 1)
- `rf_bk2423_scanner.py` (Step 4)
- `rf_tap_capture.py` (Step 5, may need capture time extended)
