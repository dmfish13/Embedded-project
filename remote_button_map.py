"""
Remote Button Map — Jasco QOBRGBXYZA 25-button membrane keypad

Pure data module defining all colors, button mappings, mode presets,
and white temperature variants. No hardware dependencies.

Color format: WRGB tuples (White, Red, Green, Blue) matching TM1815B frame order.
Values 0-255 represent PWM duty cycle per channel before dimming is applied.

Button layout on the remote (5 columns × 5 rows + 1 bottom):
    Row 1: Power | Fade | Dimming | Strobe
    Row 2: Color1 | 2-hour | Color2 | 4-hour
    Row 3: Modes | Deep Red | Mint | Dark Blue
    Row 4: Red | Orange | Light Blue | Violet
    Row 5: Green | Golden Rod | Cyan | Purple
    Row 6: Blue | Yellow | Steel Blue | Magenta
    Row 7: White
"""

# ═══════════════════════════════════════════════════════════════════════
# Color Definitions — WRGB (White, Red, Green, Blue)
# ═══════════════════════════════════════════════════════════════════════

OFF           = (0,   0,   0,   0)
NEUTRAL_WHITE = (255, 0,   0,   0)   # Pure white LED channel only

# 15 direct-color button values
DEEP_RED      = (0,   150, 5,   5)
MINT          = (0,   0,   225, 120)
DARK_BLUE     = (0,   0,   0,   139)
RED_PRIME     = (0,   255, 0,   0)
ORANGE        = (0,   255, 100, 0)
LIGHT_BLUE    = (0,   100, 150, 255)
VIOLET        = (0,   100, 0,   211)
GREEN_PRIME   = (0,   0,   255, 0)
GOLDEN_ROD    = (0,   218, 148, 0)
CYAN          = (0,   0,   255, 255)
PURPLE        = (0,   128, 0,   168)
BLUE_PRIME    = (0,   0,   0,   255)
YELLOW        = (0,   255, 230, 0)
STEEL_BLUE    = (0,   70,  130, 180)
MAGENTA       = (0,   255, 0,   255)

# White temperature variants — mix of W channel + RGB to simulate CCT
CANDLELIGHT   = (76,  255, 128, 0)   # ~1800K warm amber
WARM_WHITE    = (154, 180, 77,  0)   # ~3000K incandescent
COOL_WHITE    = (217, 0,   64,  128) # ~5000K fluorescent
DAYLIGHT      = (178, 0,   128, 255) # ~6500K blue-white

# Holiday preset colors (not on individual buttons)
FOREST_GREEN  = (0,   10,  154, 24)
IRISH_GREEN   = (0,   30,  196, 30)


# ═══════════════════════════════════════════════════════════════════════
# Button-to-Color Mapping (15 direct-color buttons)
# ═══════════════════════════════════════════════════════════════════════

COLOR_BUTTONS = {
    "Deep_Red":    DEEP_RED,
    "Mint":        MINT,
    "Dark_Blue":   DARK_BLUE,
    "Red_Prime":   RED_PRIME,
    "Orange":      ORANGE,
    "Light_Blue":  LIGHT_BLUE,
    "Violet":      VIOLET,
    "Green_Prime": GREEN_PRIME,
    "Golden_Rod":  GOLDEN_ROD,
    "Cyan":        CYAN,
    "Purple":      PURPLE,
    "Blue_Prime":  BLUE_PRIME,
    "Yellow":      YELLOW,
    "Steel_Blue":  STEEL_BLUE,
    "Magenta":     MAGENTA,
}


# ═══════════════════════════════════════════════════════════════════════
# Colors eligible for random selection in animations
# Excludes white temperatures (look washed out in rapid animations)
# ═══════════════════════════════════════════════════════════════════════

ELIGIBLE_COLORS = [
    DEEP_RED, MINT, DARK_BLUE, RED_PRIME, ORANGE, LIGHT_BLUE,
    VIOLET, GREEN_PRIME, GOLDEN_ROD, CYAN, PURPLE, BLUE_PRIME,
    YELLOW, STEEL_BLUE, MAGENTA, NEUTRAL_WHITE,
]


# ═══════════════════════════════════════════════════════════════════════
# Color display names (for console output)
# ═══════════════════════════════════════════════════════════════════════

COLOR_NAMES = {
    DEEP_RED: "Deep Red", MINT: "Mint", DARK_BLUE: "Dark Blue",
    RED_PRIME: "Red Prime", ORANGE: "Orange", LIGHT_BLUE: "Light Blue",
    VIOLET: "Violet", GREEN_PRIME: "Green Prime", GOLDEN_ROD: "Golden Rod",
    CYAN: "Cyan", PURPLE: "Purple", BLUE_PRIME: "Blue Prime",
    YELLOW: "Yellow", STEEL_BLUE: "Steel Blue", MAGENTA: "Magenta",
    NEUTRAL_WHITE: "Neutral White", CANDLELIGHT: "Candlelight",
    WARM_WHITE: "Warm White", COOL_WHITE: "Cool White", DAYLIGHT: "Daylight",
    FOREST_GREEN: "Forest Green", IRISH_GREEN: "Irish Green",
}


# ═══════════════════════════════════════════════════════════════════════
# White temperatures cycled by the White button (ordered warm → cool)
# ═══════════════════════════════════════════════════════════════════════

WHITE_TEMPS = [
    ("Candlelight ~1800K",   CANDLELIGHT),
    ("Warm White ~3000K",    WARM_WHITE),
    ("Neutral White ~4000K", NEUTRAL_WHITE),
    ("Cool White ~5000K",    COOL_WHITE),
    ("Daylight ~6500K",      DAYLIGHT),
]


# ═══════════════════════════════════════════════════════════════════════
# Modes list — cycled by the Modes button
#
# Each entry: (display_name, mode_type, color_pattern_or_None)
# "preset" entries display a static repeating color pattern.
# "chaser"/"theater"/"twinkle" entries run their respective animations.
# ═══════════════════════════════════════════════════════════════════════

MODES_LIST = [
    ("Christmas",        "preset",  [NEUTRAL_WHITE, FOREST_GREEN, DEEP_RED]),
    ("St Patrick's Day", "preset",  [IRISH_GREEN, COOL_WHITE, ORANGE]),
    ("4th of July",      "preset",  [RED_PRIME, COOL_WHITE, DARK_BLUE]),
    ("Canada",           "preset",  [DEEP_RED, COOL_WHITE, DEEP_RED]),
    ("Chaser",           "chaser",  None),
    ("Theater Chase",    "theater", None),
    ("Twinkle",          "twinkle", None),
]


# ═══════════════════════════════════════════════════════════════════════
# All 25 button names in remote layout order (used by --learn mode)
# ═══════════════════════════════════════════════════════════════════════

BUTTON_NAMES = [
    "Power", "Fade", "Dimming", "Strobe",
    "Color1", "2-hour", "Color2", "4-hour",
    "Modes",
    "Deep_Red", "Mint", "Dark_Blue",
    "Red_Prime", "Orange", "Light_Blue", "Violet",
    "Green_Prime", "Golden_Rod", "Cyan", "Purple",
    "Blue_Prime", "Yellow", "Steel_Blue", "Magenta",
    "White_Select",
]
