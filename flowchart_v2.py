#!/usr/bin/env python3
"""Generate a two-branch flowchart that merges into final integration."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

fig, ax = plt.subplots(figsize=(12, 10))
ax.set_xlim(0, 12)
ax.set_ylim(0, 10)
ax.axis("off")
fig.patch.set_facecolor("white")

# Colors
C_RF_E   = "#1A5276"
C_RF_F   = "#AED6F1"
C_LED_E  = "#7D3C98"
C_LED_F  = "#D7BDE2"
C_MERGE_E = "#784212"
C_MERGE_F = "#FAD7A0"
C_FINAL_E = "#196F3D"
C_FINAL_F = "#A9DFBF"
TEXT      = "#1B2631"
ARROW     = "#34495E"


def box(x, y, w, h, text, ec, fc, fs=12, bold=True):
    rect = mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.2",
        edgecolor=ec, facecolor=fc, linewidth=2.5
    )
    ax.add_patch(rect)
    ax.text(x + w / 2, y + h / 2, text,
            ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal",
            color=TEXT, multialignment="center")


def arrow(x1, y1, x2, y2, label="", offset=0.25):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle="-|>", color=ARROW, lw=2.5, mutation_scale=20)
    )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx + offset, my, label, fontsize=9, color=ARROW,
                fontstyle="italic", va="center")


# ── Title ──
ax.text(6, 9.6, "System Design Flowchart", ha="center",
        fontsize=18, fontweight="bold", color=TEXT)

# ── Branch labels ──
ax.text(3, 9.05, "RF Signal Path", ha="center",
        fontsize=13, fontweight="bold", color=C_RF_E)
ax.text(9, 9.05, "LED Control Path", ha="center",
        fontsize=13, fontweight="bold", color=C_LED_E)

# ── Left branch: RF ──
BW = 3.6
BH = 0.85
LX = 1.2

box(LX, 7.8, BW, BH, "Set Up RF Receiver", C_RF_E, C_RF_F)
arrow(LX + BW/2, 7.8, LX + BW/2, 7.15)

box(LX, 6.3, BW, BH, "Scan for\nRemote Data", C_RF_E, C_RF_F)
arrow(LX + BW/2, 6.3, LX + BW/2, 5.65)

box(LX, 4.8, BW, BH, "Decode\nData Packets", C_RF_E, C_RF_F)
arrow(LX + BW/2, 4.8, LX + BW/2, 4.15)

box(LX, 3.3, BW, BH, "Assign Button\nFunctionality", C_RF_E, C_RF_F)

# ── Right branch: LED ──
RX = 7.2

box(RX, 7.8, BW, BH, "LED Control", C_LED_E, C_LED_F)
arrow(RX + BW/2, 7.8, RX + BW/2, 7.15)

box(RX, 6.3, BW, BH, "RGBW Color\nConfiguration", C_LED_E, C_LED_F)
arrow(RX + BW/2, 6.3, RX + BW/2, 5.65)

box(RX, 4.8, BW, BH, "LED\nFunctionality", C_LED_E, C_LED_F)

# ── Merge arrows ──
# Left branch down to merge box
arrow(LX + BW/2, 3.3, LX + BW/2, 2.65)
# Bend right toward center
arrow(LX + BW/2, 2.65, 6, 2.15)

# Right branch down to merge area
arrow(RX + BW/2, 4.8, RX + BW/2, 2.65)
# Bend left toward center
arrow(RX + BW/2, 2.65, 6, 2.15)

# ── Merge box ──
MW = 5.0
MX = 3.5
box(MX, 1.35, MW, 0.85, "Combine RF + LED\ninto Single Controller", C_MERGE_E, C_MERGE_F, 12)

arrow(6, 1.35, 6, 0.7)

# ── Final box ──
box(MX - 0.3, -0.15, MW + 0.6, 0.85, "LED Remote Control\nFunctionality", C_FINAL_E, C_FINAL_F, 14)

# ── Dashed vertical divider ──
ax.plot([6, 6], [9.3, 4.3], color="#B0B0B0", linestyle="--", linewidth=1.2)

plt.tight_layout()
plt.savefig("/home/user/Embedded-project/system_flowchart_v2.png",
            dpi=200, bbox_inches="tight")
print("Saved system_flowchart_v2.png")
