#!/usr/bin/env python3
"""
Analyze captured RF button data for patterns.

Reads rf_capture_log.txt and looks for:
  1. Repeatable patterns (same hex per button)
  2. Sync words or preambles
  3. Bit-reversal patterns
  4. Scrambling patterns
  5. Channel preference per button

Usage:
    python3 rf_analyze_capture.py < rf_capture_log.txt
"""

import sys
import re
from collections import defaultdict, Counter


def parse_log_file(filename):
    """Parse the rf_capture_log.txt file.

    Returns:
        Dict mapping button_name -> list of (channel, hex_str) tuples
    """
    button_data = {}
    current_button = None

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()

            # Detect button header (e.g., "  Power (5 packets)")
            if line and not line.startswith("  -") and not "Date:" in line and not "Channels:" in line and not "Data Rate:" in line:
                match = re.match(r'^  (\S+)\s+\((\d+)\s+packets?\)', line)
                if match:
                    current_button = match.group(1)
                    button_data[current_button] = []
                continue

            # Parse packet lines (e.g., "    AABBCCDDEE... (3x, ch: [21, 42])")
            if current_button and line.startswith("    ") and "x, ch:" in line:
                parts = line.split("(")
                hex_str = parts[0].strip()
                match = re.search(r'\[([^\]]+)\]', parts[1])
                if match:
                    channels = [int(x.strip()) for x in match.group(1).split(",")]
                    for ch in channels:
                        button_data[current_button].append((ch, hex_str))

    return button_data


def analyze_patterns(button_data):
    """Analyze captured data for patterns.

    Args:
        button_data: Dict from parse_log_file

    Returns:
        Analysis results dict
    """
    results = {}

    for button_name, packets in button_data.items():
        if not packets:
            continue

        # Group by hex pattern
        patterns = defaultdict(list)
        for ch, hex_str in packets:
            patterns[hex_str].append(ch)

        # Sort by frequency
        sorted_patterns = sorted(patterns.items(),
                                 key=lambda x: len(x[1]),
                                 reverse=True)

        # Analyze each pattern
        unique_count = len(sorted_patterns)
        most_common_hex = sorted_patterns[0][0] if sorted_patterns else ""
        most_common_count = len(sorted_patterns[0][1]) if sorted_patterns else 0
        consistency = (most_common_count / len(packets) * 100) if packets else 0

        # Check for bit reversals
        reversals = []
        for hex_str, channels in sorted_patterns[:3]:
            # Reverse bits in each byte
            rev_bytes = []
            for i in range(0, len(hex_str), 2):
                if i+1 < len(hex_str):
                    byte_val = int(hex_str[i:i+2], 16)
                    rev_val = int(format(byte_val, '08b')[::-1], 2)
                    rev_bytes.append(f"{rev_val:02X}")
            rev_hex = "".join(rev_bytes)
            reversals.append(rev_hex)

        results[button_name] = {
            'unique_patterns': unique_count,
            'total_packets': len(packets),
            'most_common_hex': most_common_hex,
            'most_common_count': most_common_count,
            'consistency': consistency,
            'all_patterns': sorted_patterns,
            'bit_reversals': reversals,
            'channels_used': list(set(ch for _, ch in packets)),
        }

    return results


def print_analysis(results):
    """Print analysis results in a readable format."""
    print("\n" + "=" * 80)
    print("  RF CAPTURE ANALYSIS")
    print("=" * 80)

    print("\n  CONSISTENCY SUMMARY (% identical packets per button)")
    print("  " + "-" * 76)
    print(f"  {'Button':<20} {'Total':<8} {'Unique':<8} {'Consistency':<12} {'Channels'}")
    print("  " + "-" * 76)

    for button_name, analysis in sorted(results.items()):
        total = analysis['total_packets']
        unique = analysis['unique_patterns']
        consistency = analysis['consistency']
        channels = sorted(analysis['channels_used'])
        print(f"  {button_name:<20} {total:<8} {unique:<8} {consistency:>10.1f}% "
              f"    {channels}")

    print("\n  HIGHLY CONSISTENT BUTTONS (>80% same packet)")
    print("  " + "-" * 76)
    consistent = [(name, analysis) for name, analysis in results.items()
                  if analysis['consistency'] > 80]
    if consistent:
        for button_name, analysis in sorted(consistent,
                                            key=lambda x: x[1]['consistency'],
                                            reverse=True):
            hex_pattern = analysis['most_common_hex']
            consistency = analysis['consistency']
            print(f"  {button_name:<20} {consistency:>5.1f}%  {hex_pattern}")
    else:
        print("  (None found)")

    print("\n  LOW CONSISTENCY BUTTONS (<50% repeating)")
    print("  " + "-" * 76)
    inconsistent = [(name, analysis) for name, analysis in results.items()
                    if analysis['consistency'] < 50]
    if inconsistent:
        for button_name, analysis in sorted(inconsistent,
                                            key=lambda x: x[1]['consistency']):
            total = analysis['total_packets']
            unique = analysis['unique_patterns']
            consistency = analysis['consistency']
            print(f"  {button_name:<20} {consistency:>5.1f}%  "
                  f"({unique} unique from {total} packets)")
    else:
        print("  (All buttons fairly consistent)")

    print("\n  PATTERN SAMPLES (Top 3 patterns per button)")
    print("  " + "-" * 76)
    for button_name, analysis in sorted(results.items()):
        if analysis['all_patterns']:
            print(f"\n  {button_name}:")
            for hex_str, channels in analysis['all_patterns'][:3]:
                count = len(channels)
                ch_str = ", ".join(str(ch) for ch in sorted(channels))
                print(f"    {hex_str[:40]:<40} ({count}x, ch: {ch_str})")

    print("\n  CHANNEL USAGE")
    print("  " + "-" * 76)
    all_channels = set()
    for analysis in results.values():
        all_channels.update(analysis['channels_used'])

    for ch in sorted(all_channels):
        buttons_on_ch = [name for name, analysis in results.items()
                         if ch in analysis['channels_used']]
        print(f"  Channel {ch:>2} ({2400+ch} MHz): {len(buttons_on_ch):>2} buttons")

    print("\n" + "=" * 80)


def main():
    if len(sys.argv) > 1:
        logfile = sys.argv[1]
    else:
        logfile = "rf_capture_log.txt"

    try:
        button_data = parse_log_file(logfile)
        results = analyze_patterns(button_data)
        print_analysis(results)

        # Save summary
        summary_file = "rf_analysis_summary.txt"
        with open(summary_file, 'w') as f:
            f.write("RF CAPTURE ANALYSIS SUMMARY\n")
            f.write("=" * 80 + "\n\n")
            for button_name, analysis in sorted(results.items()):
                f.write(f"{button_name}:\n")
                f.write(f"  Total packets: {analysis['total_packets']}\n")
                f.write(f"  Unique patterns: {analysis['unique_patterns']}\n")
                f.write(f"  Consistency: {analysis['consistency']:.1f}%\n")
                if analysis['all_patterns']:
                    f.write(f"  Top pattern: {analysis['all_patterns'][0][0]}\n")
                f.write("\n")

        print(f"\nSummary saved to {summary_file}")

    except FileNotFoundError:
        print(f"Error: {logfile} not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing log: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
