#!/usr/bin/env python3
"""Generate a simplified high-level system flowchart as a PNG image."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

fig, ax = plt.subplots(figsize=(10, 8))
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.axis("off")
fig.patch.set_facecolor("white")

# Color palette
EDGE_INPUT  = "#2C3E50"
FILL_INPUT  = "#D5E8F0"
EDGE_PROC   = "#1A5276"
FILL_PROC   = "#AED6F1"
EDGE_OUT    = "#784212"
FILL_OUT    = "#FAD7A0"
TEXT_COLOR  = "#1B2631"
ARROW_COLOR = "#34495E"


def draw_box(x, y, w, h, text, edge, fill, fontsize=13, bold=True):
    rect = mpatches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.2",
        edgecolor=edge, facecolor=fill, linewidth=2.5
    )
    ax.add_patch(rect)
    weight = "bold" if bold else "normal"
    ax.text(x + w / 2, y + h / 2, text,
            ha="center", va="center", fontsize=fontsize,
            fontweight=weight, color=TEXT_COLOR,
            multialignment="center")


def draw_arrow(x1, y1, x2, y2, label=""):
    ax.annotate(
        "", xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle="-|>", color=ARROW_COLOR, lw=2.5,
                        mutation_scale=22)
    )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx + 0.3, my, label, fontsize=10, color=ARROW_COLOR,
                fontstyle="italic", va="center")


# Title
ax.text(5, 9.5, "System Flowchart", ha="center", va="center",
        fontsize=18, fontweight="bold", color=TEXT_COLOR)

# Block 1: Input
draw_box(2.5, 7.7, 5, 1.0, "RF Remote", EDGE_INPUT, FILL_INPUT, 14)
draw_arrow(5, 7.7, 5, 6.9, "RF Signal")

# Block 2: Receive
draw_box(2.5, 5.9, 5, 1.0, "RF Receiver", EDGE_PROC, FILL_PROC, 14)
draw_arrow(5, 5.9, 5, 5.1, "Raw Data")

# Block 3: Process
draw_box(2.5, 4.1, 5, 1.0, "Raspberry Pi", EDGE_PROC, FILL_PROC, 14)
draw_arrow(5, 4.1, 5, 3.3, "Command")

# Block 4: Drive
draw_box(2.5, 2.3, 5, 1.0, "LED Controller", EDGE_PROC, FILL_PROC, 14)
draw_arrow(5, 2.3, 5, 1.5, "Color Data")

# Block 5: Output
draw_box(2.5, 0.5, 5, 1.0, "LED Lights", EDGE_OUT, FILL_OUT, 14)

plt.tight_layout()
plt.savefig("/home/user/Embedded-project/system_flowchart_simple.png",
            dpi=200, bbox_inches="tight")
print("Saved system_flowchart_simple.png")
