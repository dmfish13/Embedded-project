"""
Button map for the Jasco QOBRGBXYZA 25-button membrane keypad remote.

Each button is mapped with:
  - label: Human-readable button name
  - hex:   Placeholder for the raw RF payload captured by the scanner
  - rgb:   Default RGBW tuple (R, G, B, W) for color buttons; None for function buttons

Once the RF scanner captures real payloads, replace the placeholder hex values
with the actual captured bytes.
"""

# RGBW tuples: (Red, Green, Blue, White) -- values 0-255
BUTTON_MAP = {
    # --- Row 1: Function buttons ---
    "Power": {
        "label": "Power",
        "hex": None,  # placeholder -- fill after RF capture
        "rgbw": None,
    },
    "Fade": {
        "label": "Fade",
        "hex": None,
        "rgbw": None,
    },
    "Dimming": {
        "label": "Dimming",
        "hex": None,
        "rgbw": None,
    },
    "Strobe": {
        "label": "Strobe",
        "hex": None,
        "rgbw": None,
    },

    # --- Row 2: Mixed function / color ---
    "Color1": {
        "label": "Color1",
        "hex": None,
        "rgbw": None,
    },
    "2-hour": {
        "label": "2-hour Timer",
        "hex": None,
        "rgbw": None,
    },
    "Color2": {
        "label": "Color2",
        "hex": None,
        "rgbw": None,
    },
    "4-hour": {
        "label": "4-hour Timer",
        "hex": None,
        "rgbw": None,
    },

    # --- Row 3: Function + colors ---
    "Modes": {
        "label": "Modes",
        "hex": None,
        "rgbw": None,
    },
    "Deep_Red": {
        "label": "Deep Red",
        "hex": None,
        "rgbw": (180, 0, 0, 0),
    },
    "Mint": {
        "label": "Mint",
        "hex": None,
        "rgbw": (0, 200, 120, 0),
    },
    "Dark_Blue": {
        "label": "Dark Blue",
        "hex": None,
        "rgbw": (0, 0, 139, 0),
    },

    # --- Row 4: Colors ---
    "Red_Prime": {
        "label": "Red Prime",
        "hex": None,
        "rgbw": (255, 0, 0, 0),
    },
    "Orange": {
        "label": "Orange",
        "hex": None,
        "rgbw": (255, 100, 0, 0),
    },
    "Light_Blue": {
        "label": "Light Blue",
        "hex": None,
        "rgbw": (100, 150, 255, 0),
    },
    "Violet": {
        "label": "Violet",
        "hex": None,
        "rgbw": (148, 0, 211, 0),
    },

    # --- Row 5: Colors ---
    "Green_Prime": {
        "label": "Green Prime",
        "hex": None,
        "rgbw": (0, 255, 0, 0),
    },
    "Yellow": {
        "label": "Yellow",
        "hex": None,
        "rgbw": (255, 255, 0, 0),
    },
    "Cyan": {
        "label": "Cyan",
        "hex": None,
        "rgbw": (0, 255, 255, 0),
    },
    "Purple": {
        "label": "Purple",
        "hex": None,
        "rgbw": (128, 0, 128, 0),
    },

    # --- Row 6: Colors ---
    "Blue_Prime": {
        "label": "Blue Prime",
        "hex": None,
        "rgbw": (0, 0, 255, 0),
    },
    "Neon_Yellow": {
        "label": "Neon Yellow",
        "hex": None,
        "rgbw": (220, 255, 0, 0),
    },
    "Steel_Blue": {
        "label": "Steel Blue",
        "hex": None,
        "rgbw": (70, 130, 180, 0),
    },
    "Magenta": {
        "label": "Magenta",
        "hex": None,
        "rgbw": (255, 0, 255, 0),
    },

    # --- Row 7: White ---
    "White_Select": {
        "label": "White Select",
        "hex": None,
        "rgbw": (0, 0, 0, 255),
    },
}


def lookup_by_hex(payload_hex):
    """Look up a button entry by its captured hex payload.

    Args:
        payload_hex: Hex string of the captured RF payload.

    Returns:
        The matching button dict, or None if no match.
    """
    for key, btn in BUTTON_MAP.items():
        if btn["hex"] is not None and btn["hex"] == payload_hex:
            return btn
    return None


def get_rgbw(button_key):
    """Return the RGBW tuple for a given button key.

    Args:
        button_key: String key from BUTTON_MAP (e.g. "Red_Prime").

    Returns:
        Tuple of (R, G, B, W) or None if the button has no color.
    """
    btn = BUTTON_MAP.get(button_key)
    if btn is None:
        return None
    return btn["rgbw"]


def list_buttons():
    """Print all buttons and their current hex mappings."""
    print(f"{'#':<4} {'Key':<16} {'Label':<18} {'Hex':<20} {'RGBW'}")
    print("-" * 75)
    for i, (key, btn) in enumerate(BUTTON_MAP.items(), start=1):
        hex_str = btn["hex"] if btn["hex"] else "(not captured)"
        rgbw_str = str(btn["rgbw"]) if btn["rgbw"] else "(function btn)"
        print(f"{i:<4} {key:<16} {btn['label']:<18} {hex_str:<20} {rgbw_str}")


if __name__ == "__main__":
    list_buttons()
