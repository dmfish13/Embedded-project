#!/usr/bin/env python3
"""Generate system architecture flowchart as a PNG image."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

fig, ax = plt.subplots(figsize=(14, 10))
ax.set_xlim(0, 14)
ax.set_ylim(0, 10)
ax.axis("off")
fig.patch.set_facecolor("white")

# ── Color palette ──
C_HW     = "#2C3E50"   # dark blue-gray for hardware boxes
C_HW_F   = "#D5E8F0"   # light fill for hardware
C_SW     = "#1A5276"   # darker blue for software boxes
C_SW_F   = "#AED6F1"   # software fill
C_ACT    = "#784212"   # brown for action/output
C_ACT_F  = "#FAD7A0"   # action fill
C_DEC    = "#7D3C98"   # purple for decision
C_DEC_F  = "#D7BDE2"   # decision fill
C_TITLE  = "#1B2631"   # title text


def draw_box(x, y, w, h, text, edge_color, fill_color, fontsize=10, bold=False):
    rect = mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.15",
        edgecolor=edge_color, facecolor=fill_color, linewidth=2
    )
    ax.add_patch(rect)
    weight = "bold" if bold else "normal"
    ax.text(x + w / 2, y + h / 2, text,
            ha="center", va="center", fontsize=fontsize,
            fontweight=weight, color=C_TITLE, wrap=True,
            multialignment="center")


def draw_arrow(x1, y1, x2, y2, label=""):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle="-|>", color="#566573", lw=2)
    )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx + 0.15, my, label, fontsize=8, color="#566573",
                fontstyle="italic", va="center")


def draw_diamond(cx, cy, w, h, text, edge_color, fill_color):
    verts = [(cx, cy + h/2), (cx + w/2, cy), (cx, cy - h/2), (cx - w/2, cy), (cx, cy + h/2)]
    from matplotlib.patches import Polygon
    diamond = Polygon(verts, closed=True, edgecolor=edge_color,
                      facecolor=fill_color, linewidth=2)
    ax.add_patch(diamond)
    ax.text(cx, cy, text, ha="center", va="center", fontsize=9,
            fontweight="bold", color=C_TITLE, multialignment="center")


# ── Title ──
ax.text(7, 9.6, "System Architecture Flowchart", ha="center", va="center",
        fontsize=16, fontweight="bold", color=C_TITLE)
ax.text(7, 9.25, "Raspberry Pi 5 Smart LED Controller", ha="center", va="center",
        fontsize=11, color="#566573")

# ── Row 1: Remote ──
draw_box(5.5, 8.1, 3, 0.7, "Jasco QOBRGBXYZA\n25-Button RF Remote", C_HW, C_HW_F, 10, True)

draw_arrow(7, 8.1, 7, 7.55, "2.4 GHz RF")

# ── Row 2: Radio ──
draw_box(4.75, 6.8, 4.5, 0.75, "nRF24L01+ Transceiver (SPI0)\nChannels 21 / 42 / 64  |  1 Mbps", C_HW, C_HW_F, 10)

draw_arrow(7, 6.8, 7, 6.25, "Raw SPI @ 1 MHz")

# ── Row 3: Pi processing ──
draw_box(4, 5.4, 6, 0.85, "Raspberry Pi 5\nPacket Capture  >>  Hex Conversion  >>  Button Lookup",
         C_SW, C_SW_F, 10, True)

draw_arrow(7, 5.4, 7, 4.85, "")

# ── Row 4: Decision diamond ──
draw_diamond(7, 4.35, 3.2, 0.9, "Payload\nMatched?", C_DEC, C_DEC_F)

# Yes arrow (down)
draw_arrow(7, 3.9, 7, 3.35, "Yes")

# No arrow (right)
draw_arrow(8.6, 4.35, 10.5, 4.35, "No")
draw_box(10.5, 4.0, 2.5, 0.7, "Log Unknown\nPacket to Console", C_ACT, C_ACT_F, 9)

# ── Row 5: State machine ──
draw_box(4.5, 2.55, 5, 0.75, "LightState Handler\nPower / Dimming / Color / Mode", C_SW, C_SW_F, 10)

draw_arrow(7, 2.55, 7, 2.0, "RGBW command")

# ── Row 6: LED controller ──
draw_box(4.75, 1.25, 4.5, 0.7, "LED Controller (SPI1)\nSK6812 RGBW  |  GRBW Byte Order", C_HW, C_HW_F, 10)

draw_arrow(7, 1.25, 7, 0.7, "Level Shifter\n3.3V -> 5V")

# ── Row 7: LEDs ──
draw_box(5, 0.05, 4, 0.65, "RGBW LED String (12 LEDs)", C_ACT, C_ACT_F, 11, True)

# ── Left sidebar: SPI0 label ──
ax.add_patch(mpatches.FancyBboxPatch(
    (0.3, 5.5), 2.2, 2.6,
    boxstyle="round,pad=0.2",
    edgecolor="#2471A3", facecolor="#EBF5FB", linewidth=1.5, linestyle="--"
))
ax.text(1.4, 7.95, "SPI0 Bus", ha="center", fontsize=10, fontweight="bold", color="#2471A3")
ax.text(1.4, 7.55, "GPIO 8 (CSN)", ha="center", fontsize=8, color="#2471A3")
ax.text(1.4, 7.25, "GPIO 25 (CE)", ha="center", fontsize=8, color="#2471A3")
ax.text(1.4, 6.95, "GPIO 11 (SCK)", ha="center", fontsize=8, color="#2471A3")
ax.text(1.4, 6.65, "GPIO 10 (MOSI)", ha="center", fontsize=8, color="#2471A3")
ax.text(1.4, 6.35, "GPIO 9 (MISO)", ha="center", fontsize=8, color="#2471A3")
ax.text(1.4, 5.95, "Baudrate: 1 MHz", ha="center", fontsize=8, color="#2471A3")
ax.text(1.4, 5.65, "Raw SPI (no library)", ha="center", fontsize=8, color="#2471A3")

# ── Left sidebar: SPI1 label ──
ax.add_patch(mpatches.FancyBboxPatch(
    (0.3, 0.8), 2.2, 1.6,
    boxstyle="round,pad=0.2",
    edgecolor="#A04000", facecolor="#FEF5E7", linewidth=1.5, linestyle="--"
))
ax.text(1.4, 2.2, "SPI1 Bus", ha="center", fontsize=10, fontweight="bold", color="#A04000")
ax.text(1.4, 1.85, "GPIO 20 (MOSI)", ha="center", fontsize=8, color="#A04000")
ax.text(1.4, 1.55, "Bi-dir Level Shifter", ha="center", fontsize=8, color="#A04000")
ax.text(1.4, 1.25, "rpi5-ws2812 Library", ha="center", fontsize=8, color="#A04000")
ax.text(1.4, 0.95, "GRBW Color Order", ha="center", fontsize=8, color="#A04000")

# ── Right sidebar: Software pipeline ──
ax.add_patch(mpatches.FancyBboxPatch(
    (11.5, 5.5), 2.2, 3.8,
    boxstyle="round,pad=0.2",
    edgecolor="#1A5276", facecolor="#EBF5FB", linewidth=1.5, linestyle="--"
))
ax.text(12.6, 9.05, "Software Pipeline", ha="center", fontsize=10, fontweight="bold", color="#1A5276")
ax.text(12.6, 8.65, "1. rf_scanner_debugger", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 8.35, "2. rf_scanner_reset", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 8.05, "3. rf_scanner", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 7.75, "4. rf_capture", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 7.45, "5. rf_sync_finder", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 7.15, "6. rf_last_try", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 6.75, "7. button_map", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 6.45, "8. led_controller", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 6.15, "9. main (integrated)", ha="center", fontsize=8, color="#1A5276")
ax.text(12.6, 5.75, "All Python 3", ha="center", fontsize=8, fontstyle="italic", color="#1A5276")

plt.tight_layout()
plt.savefig("/home/user/Embedded-project/system_architecture_flowchart.png", dpi=200, bbox_inches="tight")
print("Saved to system_architecture_flowchart.png")
