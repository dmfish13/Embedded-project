#!/usr/bin/env python3
"""
Byte-level statistical analysis of captured RF command packets.

Parses rf_tap_results.txt (output of rf_tap_capture.py) and performs:
  1. Per-byte entropy within each button
  2. Cross-button comparison at each byte position
  3. Bit-level discrimination analysis
  4. CRC-16 checks (XN297, CCITT, Modbus)
  5. Hamming distance analysis

Usage:
    python3 rf_byte_analysis.py                    # uses rf_tap_results.txt
    python3 rf_byte_analysis.py my_capture.txt     # custom input file
"""

import sys
import math
from collections import Counter, defaultdict


def parse_tap_results(filename):
    """Parse rf_tap_results.txt format, extracting command payloads per button."""
    buttons = {}
    current_button = None
    in_commands = False

    with open(filename) as f:
        for line in f:
            line = line.rstrip()

            if line.startswith("--- ") and line.endswith(" ---"):
                current_button = line[4:-4].strip()
                buttons[current_button] = []
                in_commands = False
                continue

            if "Command payloads" in line:
                in_commands = True
                continue

            if in_commands and current_button:
                stripped = line.strip()
                if stripped.startswith("Byte 14") or stripped.startswith("Tail"):
                    in_commands = False
                    continue
                if len(stripped) == 64 and all(c in '0123456789ABCDEFabcdef'
                                                for c in stripped):
                    buttons[current_button].append(
                        bytes.fromhex(stripped))

    # Remove buttons with no commands
    return {b: pkts for b, pkts in buttons.items() if pkts}


def entropy(values):
    """Shannon entropy in bits."""
    n = len(values)
    if n == 0:
        return 0.0
    counts = Counter(values)
    return -sum((c/n) * math.log2(c/n) for c in counts.values() if c > 0)


def hamming_distance(a, b):
    """Bit-level Hamming distance between two byte sequences."""
    dist = 0
    for x, y in zip(a, b):
        dist += bin(x ^ y).count('1')
    return dist


def crc16_ccitt(data, init=0xFFFF):
    """CRC-16/CCITT (poly 0x1021)."""
    crc = init
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


def crc16_modbus(data, init=0xFFFF):
    """CRC-16/Modbus (poly 0x8005, reflected)."""
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc = crc >> 1
    return crc


def xn297_crc_init(address):
    """Compute XN297 CRC initial value from address bytes."""
    return crc16_ccitt(address, init=0xFFFF)


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else "rf_tap_results.txt"
    output_file = "audit_step6_byte_analysis.txt"

    print("=" * 70)
    print("  RF Byte-Level Analysis")
    print(f"  Input:  {input_file}")
    print(f"  Output: {output_file}")
    print("=" * 70)
    print()

    buttons = parse_tap_results(input_file)

    if not buttons:
        print("  ERROR: No command packets found in input file.")
        print("  Make sure the file contains lines like:")
        print("    4C6D176547EC002D48FBDFD11649...")
        return

    total_packets = sum(len(pkts) for pkts in buttons.values())
    print(f"  Buttons with commands: {len(buttons)}")
    print(f"  Total command packets: {total_packets}")
    print()

    for b, pkts in sorted(buttons.items()):
        print(f"    {b:<16} {len(pkts):>3} packets")
    print()

    all_packets = []
    for pkts in buttons.values():
        all_packets.extend(pkts)

    with open(output_file, 'w') as log:
        log.write("RF Byte-Level Analysis\n")
        log.write(f"Input: {input_file}\n")
        log.write(f"Buttons: {len(buttons)}, Packets: {total_packets}\n")
        log.write("=" * 70 + "\n\n")

        # === Section 1: Byte stability ===
        header = "SECTION 1: BYTE STABILITY (all packets combined)"
        print(f"  {header}")
        log.write(f"{header}\n")
        log.write("-" * 70 + "\n\n")

        print(f"  {'Pos':>4}  {'Entropy':>7}  {'Unique':>6}  "
              f"{'MostCommon':>10}  {'Freq':>5}  Classification")
        log.write(f"  {'Pos':>4}  {'Entropy':>7}  {'Unique':>6}  "
                  f"{'MostCommon':>10}  {'Freq':>5}  Classification\n")

        byte_classes = []
        for pos in range(32):
            values = [p[pos] for p in all_packets if len(p) > pos]
            ent = entropy(values)
            unique = len(set(values))
            most_common, mc_count = Counter(values).most_common(1)[0]
            freq = mc_count / len(values) if values else 0

            if freq > 0.98:
                cls = "CONSTANT"
            elif freq > 0.85:
                cls = "SEMI-CONSTANT (bit errors?)"
            elif ent > 6.5:
                cls = "RANDOM/ENCRYPTED"
            else:
                cls = "VARIABLE"

            byte_classes.append(cls)
            line = (f"  {pos:>4}  {ent:>7.3f}  {unique:>6}  "
                    f"  0x{most_common:02X}     {freq:>5.1%}  {cls}")
            print(line)
            log.write(line + "\n")

        print()
        log.write("\n")

        # === Section 2: Per-button byte consistency ===
        header = "SECTION 2: PER-BUTTON BYTE CONSISTENCY"
        print(f"  {header}")
        log.write(f"\n{header}\n")
        log.write("-" * 70 + "\n\n")

        for button, pkts in sorted(buttons.items()):
            if len(pkts) < 3:
                continue
            log.write(f"  {button} ({len(pkts)} packets):\n")
            consistent_positions = []
            for pos in range(32):
                values = [p[pos] for p in pkts if len(p) > pos]
                if len(set(values)) == 1:
                    consistent_positions.append(pos)

            log.write(f"    Consistent positions: {consistent_positions}\n")
            log.write(f"    ({len(consistent_positions)}/32 bytes identical "
                      f"across all packets)\n\n")

        # === Section 3: Cross-button discrimination ===
        header = "SECTION 3: CROSS-BUTTON DISCRIMINATION"
        print(f"  {header}")
        log.write(f"\n{header}\n")
        log.write("-" * 70 + "\n\n")

        # For each byte position, find the "majority value" per button
        # and check if different buttons have different majority values
        buttons_5plus = {b: p for b, p in buttons.items() if len(p) >= 5}

        if len(buttons_5plus) >= 2:
            log.write(f"  Using {len(buttons_5plus)} buttons with 5+ packets\n\n")

            discriminator_bytes = []
            for pos in range(32):
                majorities = {}
                consistencies = {}
                for button, pkts in buttons_5plus.items():
                    values = [p[pos] for p in pkts]
                    mc_val, mc_count = Counter(values).most_common(1)[0]
                    majorities[button] = mc_val
                    consistencies[button] = mc_count / len(values)

                unique_majorities = len(set(majorities.values()))
                avg_consistency = (sum(consistencies.values()) /
                                   len(consistencies))

                if unique_majorities > 1 and avg_consistency > 0.7:
                    discriminator_bytes.append(
                        (pos, unique_majorities, avg_consistency))
                    log.write(f"  Byte {pos}: {unique_majorities} unique "
                              f"majority values, avg consistency "
                              f"{avg_consistency:.1%}\n")
                    for button, val in sorted(majorities.items()):
                        log.write(f"    {button:<16} -> 0x{val:02X} "
                                  f"({consistencies[button]:.0%})\n")
                    log.write("\n")

            if discriminator_bytes:
                print(f"    Found {len(discriminator_bytes)} candidate "
                      f"discriminator byte(s)")
                for pos, uniq, cons in discriminator_bytes:
                    print(f"      Byte {pos}: {uniq} unique values, "
                          f"avg consistency {cons:.0%}")
            else:
                print("    No discriminator bytes found (all bytes are "
                      "either constant or random)")
        else:
            print("    Not enough buttons with 5+ packets for cross-button "
                  "analysis")
            log.write("  Not enough data for cross-button analysis.\n")

        print()
        log.write("\n")

        # === Section 4: Bit-level analysis of byte 14 ===
        header = "SECTION 4: BIT-LEVEL ANALYSIS (bytes 14-17)"
        print(f"  {header}")
        log.write(f"\n{header}\n")
        log.write("-" * 70 + "\n\n")

        for byte_pos in range(14, min(18, 32)):
            log.write(f"  Byte {byte_pos} bit analysis:\n")
            for bit in range(8):
                bit_values = defaultdict(list)
                for button, pkts in buttons_5plus.items():
                    bits = [(p[byte_pos] >> bit) & 1 for p in pkts
                            if len(p) > byte_pos]
                    if bits:
                        ones = sum(bits)
                        bit_values[button] = ones / len(bits)

                if bit_values:
                    avg_rate = sum(bit_values.values()) / len(bit_values)
                    spread = max(bit_values.values()) - min(bit_values.values())
                    log.write(f"    bit {bit}: avg_1_rate={avg_rate:.2f}, "
                              f"spread={spread:.2f}")
                    if avg_rate > 0.95:
                        log.write(" (always 1)")
                    elif avg_rate < 0.05:
                        log.write(" (always 0)")
                    elif spread > 0.5:
                        log.write(" (POSSIBLE DISCRIMINATOR)")
                    log.write("\n")
            log.write("\n")

        # === Section 5: CRC analysis ===
        header = "SECTION 5: CRC ANALYSIS"
        print(f"  {header}")
        log.write(f"\n{header}\n")
        log.write("-" * 70 + "\n\n")

        # Test CRC-16 on last 2 bytes for various payload lengths
        xn297_addr = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        xn297_init = xn297_crc_init(xn297_addr)

        crc_tests = [
            ("CRC-16/CCITT (init=0xFFFF)", lambda d: crc16_ccitt(d, 0xFFFF)),
            ("CRC-16/CCITT (init=0x0000)", lambda d: crc16_ccitt(d, 0x0000)),
            (f"CRC-16/XN297 (init=0x{xn297_init:04X})",
             lambda d: crc16_ccitt(d, xn297_init)),
            ("CRC-16/Modbus", lambda d: crc16_modbus(d, 0xFFFF)),
        ]

        for crc_name, crc_func in crc_tests:
            # Test: CRC over bytes 0..29, compare to bytes 30..31
            matches_30 = 0
            matches_28 = 0
            for pkt in all_packets:
                if len(pkt) >= 32:
                    computed_30 = crc_func(pkt[:30])
                    actual_30 = (pkt[30] << 8) | pkt[31]
                    if computed_30 == actual_30:
                        matches_30 += 1

                    computed_28 = crc_func(pkt[:28])
                    actual_28 = (pkt[28] << 8) | pkt[29]
                    if computed_28 == actual_28:
                        matches_28 += 1

            # Also test reversed byte order
            matches_30r = 0
            matches_28r = 0
            for pkt in all_packets:
                if len(pkt) >= 32:
                    computed_30 = crc_func(pkt[:30])
                    actual_30r = (pkt[31] << 8) | pkt[30]
                    if computed_30 == actual_30r:
                        matches_30r += 1

                    computed_28 = crc_func(pkt[:28])
                    actual_28r = (pkt[29] << 8) | pkt[28]
                    if computed_28 == actual_28r:
                        matches_28r += 1

            line = (f"  {crc_name}:\n"
                    f"    bytes 0-29 vs 30-31: {matches_30}/{total_packets} "
                    f"({matches_30/total_packets:.0%})\n"
                    f"    bytes 0-27 vs 28-29: {matches_28}/{total_packets} "
                    f"({matches_28/total_packets:.0%})\n"
                    f"    (reversed byte order: "
                    f"{matches_30r}/{total_packets}, "
                    f"{matches_28r}/{total_packets})\n")
            print(line.rstrip())
            log.write(line + "\n")

        # === Section 6: Hamming distances ===
        header = "SECTION 6: HAMMING DISTANCE ANALYSIS"
        print(f"\n  {header}")
        log.write(f"\n{header}\n")
        log.write("-" * 70 + "\n\n")

        # Intra-button distances (bytes 0-13 only — the "header")
        log.write("  Intra-button Hamming distance (bytes 0-13):\n")
        for button, pkts in sorted(buttons.items()):
            if len(pkts) < 2:
                continue
            dists = []
            for i in range(len(pkts)):
                for j in range(i+1, min(len(pkts), i+5)):
                    dists.append(hamming_distance(pkts[i][:14], pkts[j][:14]))
            if dists:
                avg_d = sum(dists) / len(dists)
                max_d = max(dists)
                log.write(f"    {button:<16} avg={avg_d:.1f} bits, "
                          f"max={max_d} bits ({len(dists)} pairs)\n")

        log.write("\n  Interpretation:\n")
        log.write("    0-2 bits: identical (bit errors only)\n")
        log.write("    3-10 bits: partial variation (some bytes differ)\n")
        log.write("    11+ bits: significantly different\n\n")

        # Inter-button distances (bytes 14-31 — the "variable" region)
        log.write("  Inter-button Hamming distance (bytes 14-31):\n")
        button_list = sorted(buttons_5plus.keys())
        for i in range(len(button_list)):
            for j in range(i+1, min(len(button_list), i+3)):
                b1, b2 = button_list[i], button_list[j]
                dists = []
                for p1 in buttons_5plus[b1][:5]:
                    for p2 in buttons_5plus[b2][:5]:
                        dists.append(
                            hamming_distance(p1[14:], p2[14:]))
                avg_d = sum(dists) / len(dists) if dists else 0
                log.write(f"    {b1} vs {b2}: avg={avg_d:.1f} bits\n")

        log.write("\n  Interpretation:\n")
        log.write("    ~72 bits (50%): random/encrypted (no correlation)\n")
        log.write("    <50 bits: possible shared structure\n")
        log.write("    >90 bits: anti-correlated (unusual)\n")

    print()
    print(f"  Results saved to {output_file}")
    print()
    print("  Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
