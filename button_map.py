"""
Button map for the Jasco QOBRGBXYZA 25-button membrane keypad remote.

Each button is mapped with:
  - label: Human-readable button name
  - hex:   Placeholder for the raw RF payload captured by the scanner
  - wrgb:  Default WRGB tuple (W, R, G, B) for color buttons; None for function buttons

Once the RF scanner captures real payloads, replace the placeholder hex values
with the actual captured bytes.
"""

# WRGB tuples: (White, Red, Green, Blue) -- values 0-255
BUTTON_MAP = {
    # --- Row 1: Function buttons ---
    "Power": {
        "label": "Power",
        "hex": None,  # placeholder -- fill after RF capture
        "wrgb": None,
    },
    "Fade": {
        "label": "Fade",
        "hex": None,
        "wrgb": None,
    },
    "Dimming": {
        "label": "Dimming",
        "hex": None,
        "wrgb": None,
    },
    "Strobe": {
        "label": "Strobe",
        "hex": None,
        "wrgb": None,
    },

    # --- Row 2: Mixed function / color ---
    "Color1": {
        "label": "Color1",
        "hex": None,
        "wrgb": None,
    },
    "2-hour": {
        "label": "2-hour Timer",
        "hex": None,
        "wrgb": None,
    },
    "Color2": {
        "label": "Color2",
        "hex": None,
        "wrgb": None,
    },
    "4-hour": {
        "label": "4-hour Timer",
        "hex": None,
        "wrgb": None,
    },

    # --- Row 3: Function + colors ---
    "Modes": {
        "label": "Modes",
        "hex": None,
        "wrgb": None,
    },
    "Deep_Red": {
        "label": "Deep Red",
        "hex": None,
        "wrgb": (0, 180, 0, 0),
    },
    "Mint": {
        "label": "Mint",
        "hex": None,
        "wrgb": (0, 0, 225, 120),
    },
    "Dark_Blue": {
        "label": "Dark Blue",
        "hex": None,
        "wrgb": (0, 0, 0, 139),
    },

    # --- Row 4: Colors ---
    "Red_Prime": {
        "label": "Red Prime",
        "hex": None,
        "wrgb": (0, 255, 0, 0),
    },
    "Orange": {
        "label": "Orange",
        "hex": None,
        "wrgb": (0, 255, 100, 0),
    },
    "Light_Blue": {
        "label": "Light Blue",
        "hex": None,
        "wrgb": (0, 100, 150, 255),
    },
    "Violet": {
        "label": "Violet",
        "hex": None,
        "wrgb": (0, 148, 0, 211),
    },

    # --- Row 5: Colors ---
    "Green_Prime": {
        "label": "Green Prime",
        "hex": None,
        "wrgb": (0, 0, 255, 0),
    },
    "Golden_Rod": {
        "label": "Golden Rod",
        "hex": None,
        "wrgb": (0, 218, 148, 0),
    },
    "Cyan": {
        "label": "Cyan",
        "hex": None,
        "wrgb": (0, 0, 255, 255),
    },
    "Purple": {
        "label": "Purple",
        "hex": None,
        "wrgb": (0, 128, 0, 128),
    },

    # --- Row 6: Colors ---
    "Blue_Prime": {
        "label": "Blue Prime",
        "hex": None,
        "wrgb": (0, 0, 0, 255),
    },
    "Yellow": {
        "label": "Yellow",
        "hex": None,
        "wrgb": (0, 255, 230, 0),
    },
    "Steel_Blue": {
        "label": "Steel Blue",
        "hex": None,
        "wrgb": (0, 70, 130, 180),
    },
    "Magenta": {
        "label": "Magenta",
        "hex": None,
        "wrgb": (0, 255, 0, 255),
    },

    # --- Row 7: White ---
    "White_Select": {
        "label": "White Select",
        "hex": None,
        "wrgb": (255, 0, 0, 0),
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


def get_wrgb(button_key):
    """Return the WRGB tuple for a given button key.

    Args:
        button_key: String key from BUTTON_MAP (e.g. "Red_Prime").

    Returns:
        Tuple of (W, R, G, B) or None if the button has no color.
    """
    btn = BUTTON_MAP.get(button_key)
    if btn is None:
        return None
    return btn["wrgb"]


def list_buttons():
    """Print all buttons and their current hex mappings."""
    print(f"{'#':<4} {'Key':<16} {'Label':<18} {'Hex':<20} {'WRGB'}")
    print("-" * 75)
    for i, (key, btn) in enumerate(BUTTON_MAP.items(), start=1):
        hex_str = btn["hex"] if btn["hex"] else "(not captured)"
        wrgb_str = str(btn["wrgb"]) if btn["wrgb"] else "(function btn)"
        print(f"{i:<4} {key:<16} {btn['label']:<18} {hex_str:<20} {wrgb_str}")


if __name__ == "__main__":
    list_buttons()
