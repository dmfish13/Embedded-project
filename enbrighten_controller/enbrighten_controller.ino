/*
 * Enbrighten RGBW LED Cafe Lights — RF Remote Controller
 * Arduino Nano Every (ATmega4809) port of main.py
 * ---------------------------------------------------------------------------
 * This sketch intercepts the RF signal from a Jasco QOBRGBXYZA 25-button
 * remote (XNS1042 transmitter, XN297L protocol) using an nRF24L01+ module and
 * drives a TM1815B RGBW LED cafe-light string directly.
 *
 * It is a faithful port of the Raspberry Pi 5 Python implementation (main.py),
 * adapted to the constraints of an 8-bit AVR with no operating system:
 *
 *   Python (Raspberry Pi)            Arduino Nano Every (this sketch)
 *   ----------------------           --------------------------------
 *   4 threads (RF/SPI/anim/timer) -> single cooperative loop() + millis()
 *   2 SPI buses (radio + LEDs)    -> HW-SPI for LEDs, SW-SPI for the radio
 *   1 KB encoding LUT in RAM      -> encode-on-the-fly, rebuild only on change
 *   button_codes.json on disk     -> CRC-32 fingerprints stored in EEPROM
 *   Python floats for dimming     -> integer math (0-255 scale)
 *
 * --------------------------------------------------------------------------
 * WIRING (Arduino Nano Every)
 *
 *   nRF24L01+  (software SPI — isolated from the LED data line)
 *     CE    -> D6        VCC -> 3.3V        (NOT 5V!)
 *     CSN   -> D5        GND -> GND
 *     SCK   -> D2        MOSI(radio) -> D3  MISO(radio) -> D4
 *
 *   TM1815B LED string (hardware SPI MOSI only)
 *     DATA  -> D11 (MOSI) through a 3.3V->5V level shifter
 *     (D13/SCK is driven by the SPI peripheral but left unconnected)
 *
 * The radio uses bit-banged GPIO so its SPI traffic never appears on D11 and
 * cannot corrupt the always-listening LED data line — this recreates the
 * dual-bus isolation the Raspberry Pi got for free with SPI0 + SPI1.
 *
 * --------------------------------------------------------------------------
 * USAGE
 *   - Normal operation starts automatically on power-up.
 *   - Send 'L' over the USB serial monitor (115200 baud) to enter learning
 *     mode and capture each button's code (stored in EEPROM, persists across
 *     reboots).
 *   - Send 'C' to clear learned codes.
 * ===========================================================================
 */

#include <SPI.h>
#include <EEPROM.h>

// Pixel type is declared up front so the Arduino IDE's auto-generated
// function prototypes (inserted before the first function) can reference it.
struct Pixel { uint8_t w, r, g, b; };

// ═══════════════════════════════════════════════════════════════════════
// Pin assignments
// ═══════════════════════════════════════════════════════════════════════
const uint8_t PIN_RF_SCK  = 2;   // nRF24L01+ SCK   (software SPI)
const uint8_t PIN_RF_MOSI = 3;   // nRF24L01+ MOSI  (software SPI)
const uint8_t PIN_RF_MISO = 4;   // nRF24L01+ MISO  (software SPI)
const uint8_t PIN_RF_CSN  = 5;   // nRF24L01+ CSN   (active-low chip select)
const uint8_t PIN_RF_CE   = 6;   // nRF24L01+ CE    (HIGH = RX active)
// LED data goes out on the hardware SPI MOSI pin (D11) automatically.

// ═══════════════════════════════════════════════════════════════════════
// LED constants (TM1815B)
// ═══════════════════════════════════════════════════════════════════════
const uint8_t  NUM_LEDS    = 6;
const uint8_t  RESET_BYTES = 80;      // 0xFF idle-HIGH bytes for latch/reset
const uint32_t LED_SPI_HZ  = 2000000; // 2.0 MHz — clean divider, 4 SPI bits/data bit

// TM1815B current-setting registers: C1 = ~50% of 30 mA, C2 = ~C1
const uint8_t C1_REG[4] = {0x20, 0x20, 0x20, 0x20};
const uint8_t C2_REG[4] = {0xDF, 0xDF, 0xDF, 0xDF};

// Frame layout: [RESET][C1 4B->16][C2 4B->16][6 LEDs *16][RESET]
const uint16_t FRAME_SIZE = RESET_BYTES + 16 + 16 + (uint16_t)NUM_LEDS * 16 + RESET_BYTES;
uint8_t frameBuf[FRAME_SIZE];

// Dimmer levels cycled by the Dimming button (0.1..1.0 on a 0-255 scale)
const uint8_t DIMMER_STEPS[10] = {26, 51, 77, 102, 128, 153, 179, 204, 230, 255};

// ── Animation timing (milliseconds) ──
const uint16_t FADE_DURATION_MS   = 1500;
const uint16_t FADE_HOLD_MS       = 500;
const uint16_t STROBE_INTERVAL_MS = 1250;
const uint16_t CHASER_INTERVAL_MS = 350;
const uint16_t THEATER_INTERVAL_MS = 300;
const uint16_t TWINKLE_FADE_DOWN_MS = 1250;
const uint16_t TWINKLE_FADE_UP_MS   = 1250;
const uint16_t TWINKLE_HOLD_MS      = 500;
const uint16_t TWINKLE_TICK_MS      = 30;

const uint16_t DEBOUNCE_MS = 300;

// ═══════════════════════════════════════════════════════════════════════
// RF constants (XNS1042 / XN297L protocol)
// ═══════════════════════════════════════════════════════════════════════
const uint8_t ADDR_WIDTH = 5;
const uint8_t RF_CHANNEL  = 42;                       // 2442 MHz
const uint8_t NRF24_ADDR[5] = {0x38, 0x72, 0x2D, 0xA8, 0x5E};

// Known background-source headers (descrambled first 4 bytes) — filtered out
const uint8_t COMMAND_HEADER[4] = {0x4C, 0x6D, 0x17, 0x65};
const uint8_t IDLE_HEADER[4]    = {0x4C, 0xED, 0xAD, 0x0B};

// XN297L Scramble Table B (stored in flash to save SRAM)
const uint8_t SCRAMBLE_B[32] PROGMEM = {
    0xE3, 0xB1, 0x4B, 0xEA, 0x85, 0xBC, 0xE5, 0x66,
    0x0D, 0xAE, 0x8C, 0x88, 0x12, 0x69, 0xEE, 0x1F,
    0xC7, 0x62, 0x97, 0xD5, 0x0B, 0x79, 0xCA, 0xCC,
    0x1B, 0x5D, 0x19, 0x10, 0x24, 0xD3, 0xDC, 0x3F,
};

// Reverse the bit order of a byte (BIT_REVERSE[0xC0] == 0x03)
uint8_t bitReverse(uint8_t b) {
    b = (b & 0xF0) >> 4 | (b & 0x0F) << 4;
    b = (b & 0xCC) >> 2 | (b & 0x33) << 2;
    b = (b & 0xAA) >> 1 | (b & 0x55) << 1;
    return b;
}

// ═══════════════════════════════════════════════════════════════════════
// Color definitions — WRGB (White, Red, Green, Blue), matching TM1815B order
// (Pixel struct is declared near the top of the file.)
// ═══════════════════════════════════════════════════════════════════════
const Pixel OFF           = {0,   0,   0,   0};
const Pixel NEUTRAL_WHITE = {255, 0,   0,   0};
const Pixel DEEP_RED      = {0,   150, 5,   5};
const Pixel MINT          = {0,   0,   225, 120};
const Pixel DARK_BLUE     = {0,   0,   0,   139};
const Pixel RED_PRIME     = {0,   255, 0,   0};
const Pixel ORANGE        = {0,   255, 100, 0};
const Pixel LIGHT_BLUE    = {0,   100, 150, 255};
const Pixel VIOLET        = {0,   100, 0,   211};
const Pixel GREEN_PRIME   = {0,   0,   255, 0};
const Pixel GOLDEN_ROD    = {0,   218, 148, 0};
const Pixel CYAN          = {0,   0,   255, 255};
const Pixel PURPLE        = {0,   128, 0,   168};
const Pixel BLUE_PRIME    = {0,   0,   0,   255};
const Pixel YELLOW        = {0,   255, 230, 0};
const Pixel STEEL_BLUE    = {0,   70,  130, 180};
const Pixel MAGENTA       = {0,   255, 0,   255};
const Pixel CANDLELIGHT   = {76,  255, 128, 0};
const Pixel WARM_WHITE    = {154, 180, 77,  0};
const Pixel COOL_WHITE    = {217, 0,   64,  128};
const Pixel DAYLIGHT      = {178, 0,   128, 255};
const Pixel FOREST_GREEN  = {0,   10,  154, 24};
const Pixel IRISH_GREEN   = {0,   30,  196, 30};

// White temperatures cycled by the White button (warm -> cool, start idx 2)
const Pixel WHITE_TEMPS[5] = {CANDLELIGHT, WARM_WHITE, NEUTRAL_WHITE, COOL_WHITE, DAYLIGHT};

// Colors eligible for random selection in Fade/Strobe/Chaser/Twinkle
const Pixel ELIGIBLE_COLORS[16] = {
    DEEP_RED, MINT, DARK_BLUE, RED_PRIME, ORANGE, LIGHT_BLUE,
    VIOLET, GREEN_PRIME, GOLDEN_ROD, CYAN, PURPLE, BLUE_PRIME,
    YELLOW, STEEL_BLUE, MAGENTA, NEUTRAL_WHITE,
};

bool pixelEq(const Pixel &a, const Pixel &b) {
    return a.w == b.w && a.r == b.r && a.g == b.g && a.b == b.b;
}

// ═══════════════════════════════════════════════════════════════════════
// Button identifiers (same order as BUTTON_NAMES in main.py)
// ═══════════════════════════════════════════════════════════════════════
enum {
    BTN_POWER, BTN_FADE, BTN_DIMMING, BTN_STROBE,
    BTN_COLOR1, BTN_2HOUR, BTN_COLOR2, BTN_4HOUR,
    BTN_MODES,
    BTN_DEEP_RED, BTN_MINT, BTN_DARK_BLUE,
    BTN_RED_PRIME, BTN_ORANGE, BTN_LIGHT_BLUE, BTN_VIOLET,
    BTN_GREEN_PRIME, BTN_GOLDEN_ROD, BTN_CYAN, BTN_PURPLE,
    BTN_BLUE_PRIME, BTN_YELLOW, BTN_STEEL_BLUE, BTN_MAGENTA,
    BTN_WHITE_SELECT,
    NUM_BUTTONS
};

const __FlashStringHelper *buttonName(uint8_t id) {
    switch (id) {
        case BTN_POWER:       return F("Power");
        case BTN_FADE:        return F("Fade");
        case BTN_DIMMING:     return F("Dimming");
        case BTN_STROBE:      return F("Strobe");
        case BTN_COLOR1:      return F("Color1");
        case BTN_2HOUR:       return F("2-hour");
        case BTN_COLOR2:      return F("Color2");
        case BTN_4HOUR:       return F("4-hour");
        case BTN_MODES:       return F("Modes");
        case BTN_DEEP_RED:    return F("Deep_Red");
        case BTN_MINT:        return F("Mint");
        case BTN_DARK_BLUE:   return F("Dark_Blue");
        case BTN_RED_PRIME:   return F("Red_Prime");
        case BTN_ORANGE:      return F("Orange");
        case BTN_LIGHT_BLUE:  return F("Light_Blue");
        case BTN_VIOLET:      return F("Violet");
        case BTN_GREEN_PRIME: return F("Green_Prime");
        case BTN_GOLDEN_ROD:  return F("Golden_Rod");
        case BTN_CYAN:        return F("Cyan");
        case BTN_PURPLE:      return F("Purple");
        case BTN_BLUE_PRIME:  return F("Blue_Prime");
        case BTN_YELLOW:      return F("Yellow");
        case BTN_STEEL_BLUE:  return F("Steel_Blue");
        case BTN_MAGENTA:     return F("Magenta");
        case BTN_WHITE_SELECT:return F("White_Select");
        default:              return F("?");
    }
}

// Map a color-button id to its WRGB pixel; returns false for non-color buttons
bool colorForButton(uint8_t id, Pixel &out) {
    switch (id) {
        case BTN_DEEP_RED:    out = DEEP_RED;    return true;
        case BTN_MINT:        out = MINT;        return true;
        case BTN_DARK_BLUE:   out = DARK_BLUE;   return true;
        case BTN_RED_PRIME:   out = RED_PRIME;   return true;
        case BTN_ORANGE:      out = ORANGE;      return true;
        case BTN_LIGHT_BLUE:  out = LIGHT_BLUE;  return true;
        case BTN_VIOLET:      out = VIOLET;      return true;
        case BTN_GREEN_PRIME: out = GREEN_PRIME; return true;
        case BTN_GOLDEN_ROD:  out = GOLDEN_ROD;  return true;
        case BTN_CYAN:        out = CYAN;        return true;
        case BTN_PURPLE:      out = PURPLE;      return true;
        case BTN_BLUE_PRIME:  out = BLUE_PRIME;  return true;
        case BTN_YELLOW:      out = YELLOW;      return true;
        case BTN_STEEL_BLUE:  out = STEEL_BLUE;  return true;
        case BTN_MAGENTA:     out = MAGENTA;     return true;
        default:              return false;
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Modes list — cycled by the Modes button
// ═══════════════════════════════════════════════════════════════════════
enum { MT_PRESET, MT_CHASER, MT_THEATER, MT_TWINKLE };
struct ModeEntry { uint8_t type; Pixel p0, p1, p2; };

const ModeEntry MODES_LIST[7] = {
    {MT_PRESET,  NEUTRAL_WHITE, FOREST_GREEN, DEEP_RED},   // Christmas
    {MT_PRESET,  IRISH_GREEN,   COOL_WHITE,   ORANGE},     // St Patrick's Day
    {MT_PRESET,  RED_PRIME,     COOL_WHITE,   DARK_BLUE},  // 4th of July
    {MT_PRESET,  DEEP_RED,      COOL_WHITE,   DEEP_RED},   // Canada
    {MT_CHASER,  OFF, OFF, OFF},
    {MT_THEATER, OFF, OFF, OFF},
    {MT_TWINKLE, OFF, OFF, OFF},
};

const __FlashStringHelper *modeName(uint8_t idx) {
    switch (idx) {
        case 0: return F("Christmas");
        case 1: return F("St Patrick's Day");
        case 2: return F("4th of July");
        case 3: return F("Canada");
        case 4: return F("Chaser");
        case 5: return F("Theater Chase");
        case 6: return F("Twinkle");
        default: return F("?");
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Pixel math (integer)
// ═══════════════════════════════════════════════════════════════════════
uint8_t scale8(uint8_t v, uint8_t dim) { return ((uint16_t)v * dim) / 255; }

Pixel dimPixel(const Pixel &p, uint8_t dim) {
    return {scale8(p.w, dim), scale8(p.r, dim), scale8(p.g, dim), scale8(p.b, dim)};
}

// Linear interpolation, t in 0..256 (0 -> a, 256 -> b)
uint8_t lerp8(uint8_t a, uint8_t b, uint16_t t) {
    return (uint8_t)((int32_t)a + ((int32_t)(b - a) * (int32_t)t) / 256);
}
Pixel lerpPixel(const Pixel &a, const Pixel &b, uint16_t t) {
    return {lerp8(a.w, b.w, t), lerp8(a.r, b.r, t), lerp8(a.g, b.g, t), lerp8(a.b, b.b, t)};
}

Pixel pickRandom(const Pixel *exclude) {
    while (true) {
        Pixel c = ELIGIBLE_COLORS[random(16)];
        if (!exclude || !pixelEq(c, *exclude)) return c;
    }
}

// ═══════════════════════════════════════════════════════════════════════
// TM1815B encoding & frame construction
//
// Each data bit -> 4 SPI bits at 2.0 MHz (500 ns each):
//   Logic 1 -> 0b0001 (LOW 1500 ns, HIGH 500 ns)
//   Logic 0 -> 0b0111 (LOW 500 ns,  HIGH 1500 ns)
// The frame is only rebuilt when the displayed content changes; showFrame()
// re-transmits the cached buffer every loop (continuous refresh).
// ═══════════════════════════════════════════════════════════════════════
void encodeByte(uint8_t *dst, uint8_t value) {
    uint32_t enc = 0;
    for (int8_t bit = 7; bit >= 0; bit--) {
        enc = (enc << 4) | ((value & (1 << bit)) ? 0x1 : 0x7);
    }
    dst[0] = (enc >> 24) & 0xFF;
    dst[1] = (enc >> 16) & 0xFF;
    dst[2] = (enc >> 8)  & 0xFF;
    dst[3] = enc & 0xFF;
}

void buildFrame(const Pixel *pixels) {
    uint16_t i = 0;
    for (uint8_t k = 0; k < RESET_BYTES; k++) frameBuf[i++] = 0xFF;
    for (uint8_t k = 0; k < 4; k++) { encodeByte(&frameBuf[i], C1_REG[k]); i += 4; }
    for (uint8_t k = 0; k < 4; k++) { encodeByte(&frameBuf[i], C2_REG[k]); i += 4; }
    for (uint8_t n = 0; n < NUM_LEDS; n++) {
        encodeByte(&frameBuf[i], pixels[n].w); i += 4;
        encodeByte(&frameBuf[i], pixels[n].r); i += 4;
        encodeByte(&frameBuf[i], pixels[n].g); i += 4;
        encodeByte(&frameBuf[i], pixels[n].b); i += 4;
    }
    for (uint8_t k = 0; k < RESET_BYTES; k++) frameBuf[i++] = 0xFF;
}

void buildSolid(const Pixel &wrgb, uint8_t dim) {
    Pixel px[NUM_LEDS];
    Pixel p = dimPixel(wrgb, dim);
    for (uint8_t n = 0; n < NUM_LEDS; n++) px[n] = p;
    buildFrame(px);
}

void buildDual(const Pixel &c1, const Pixel &c2, uint8_t dim) {
    Pixel px[NUM_LEDS];
    for (uint8_t n = 0; n < NUM_LEDS; n++) {
        px[n] = dimPixel((n % 2 == 0) ? c1 : c2, dim);
    }
    buildFrame(px);
}

// Continuously re-transmit the cached frame. Byte-at-a-time SPI.transfer is
// used (not the block form) so the radio's returned data never overwrites
// frameBuf, and so MOSI idles HIGH (last reset byte) between frames.
void showFrame() {
    for (uint16_t i = 0; i < FRAME_SIZE; i++) SPI.transfer(frameBuf[i]);
}

// ═══════════════════════════════════════════════════════════════════════
// nRF24L01+ driver — bit-banged software SPI (RX only)
// ═══════════════════════════════════════════════════════════════════════
uint8_t rfTransfer(uint8_t out) {
    uint8_t in = 0;
    for (int8_t i = 7; i >= 0; i--) {            // MSB first, SPI mode 0
        digitalWrite(PIN_RF_MOSI, (out >> i) & 1);
        digitalWrite(PIN_RF_SCK, HIGH);
        in = (in << 1) | (digitalRead(PIN_RF_MISO) & 1);
        digitalWrite(PIN_RF_SCK, LOW);
    }
    return in;
}

void rfWriteReg(uint8_t reg, uint8_t val) {
    digitalWrite(PIN_RF_CSN, LOW);
    rfTransfer(0x20 | reg);
    rfTransfer(val);
    digitalWrite(PIN_RF_CSN, HIGH);
}

void rfWriteRegMulti(uint8_t reg, const uint8_t *data, uint8_t len) {
    digitalWrite(PIN_RF_CSN, LOW);
    rfTransfer(0x20 | reg);
    for (uint8_t i = 0; i < len; i++) rfTransfer(data[i]);
    digitalWrite(PIN_RF_CSN, HIGH);
}

uint8_t rfReadReg(uint8_t reg) {
    digitalWrite(PIN_RF_CSN, LOW);
    rfTransfer(reg);
    uint8_t val = rfTransfer(0xFF);
    digitalWrite(PIN_RF_CSN, HIGH);
    return val;
}

void rfCmd(uint8_t cmd) {
    digitalWrite(PIN_RF_CSN, LOW);
    rfTransfer(cmd);
    digitalWrite(PIN_RF_CSN, HIGH);
}

void rfConfigure() {
    digitalWrite(PIN_RF_CE, LOW);
    rfWriteReg(0x00, 0x03);                 // CONFIG: PWR_UP | PRIM_RX
    delay(2);                               // power-up settle
    rfWriteReg(0x01, 0x00);                 // EN_AA: no auto-ack
    rfWriteReg(0x02, 0x01);                 // EN_RXADDR: pipe 0 only
    rfWriteReg(0x03, ADDR_WIDTH - 2);       // SETUP_AW: 5-byte address
    rfWriteRegMulti(0x0A, NRF24_ADDR, 5);   // RX_ADDR_P0
    rfWriteReg(0x11, 32);                   // RX_PW_P0: 32-byte payload
    rfWriteReg(0x06, 0x07);                 // RF_SETUP: 1 Mbps, 0 dBm
    rfWriteReg(0x1C, 0x00);                 // DYNPD off
    rfWriteReg(0x1D, 0x00);                 // FEATURE off
    rfCmd(0xE2);                            // FLUSH_RX
    rfCmd(0xE1);                            // FLUSH_TX
    rfWriteReg(0x07, 0x70);                 // clear status flags
    rfWriteReg(0x05, RF_CHANNEL);           // RF_CH 42
    digitalWrite(PIN_RF_CE, HIGH);          // enter RX mode
}

bool rfAvailable() {
    return !(rfReadReg(0x17) & 0x01);       // FIFO_STATUS RX_EMPTY == 0
}

void rfReadPayload(uint8_t *buf) {
    digitalWrite(PIN_RF_CSN, LOW);
    rfTransfer(0x61);                       // R_RX_PAYLOAD
    for (uint8_t i = 0; i < 32; i++) buf[i] = rfTransfer(0xFF);
    digitalWrite(PIN_RF_CSN, HIGH);
    rfWriteReg(0x07, 0x70);                 // clear flags
}

void rfPowerDown() {
    digitalWrite(PIN_RF_CE, LOW);
    rfWriteReg(0x00, 0x00);
}

// ═══════════════════════════════════════════════════════════════════════
// XN297L descramble + button fingerprinting
// ═══════════════════════════════════════════════════════════════════════
void descramble(const uint8_t *raw, uint8_t len, uint8_t *out) {
    for (uint8_t i = 0; i < len; i++) {
        uint8_t b = bitReverse(raw[i]);
        uint8_t idx = ADDR_WIDTH + i;
        if (idx < 32) b ^= pgm_read_byte(&SCRAMBLE_B[idx]);
        out[i] = b;
    }
}

bool isBackgroundPacket(const uint8_t *desc) {
    bool cmd = true, idle = true;
    for (uint8_t i = 0; i < 4; i++) {
        if (desc[i] != COMMAND_HEADER[i]) cmd = false;
        if (desc[i] != IDLE_HEADER[i])    idle = false;
    }
    return cmd || idle;
}

// Find where meaningful payload ends and the idle-line tail begins.
// Mirrors find_idle_start(): convert back to raw on-air bytes and scan
// backward for a repeating 0xFF/0x55/0xAA tail (±1 bit error tolerance).
uint8_t findIdleStart(const uint8_t *desc, uint8_t len) {
    uint8_t raw[32];
    for (uint8_t i = 0; i < len; i++) {
        uint8_t idx = ADDR_WIDTH + i;
        uint8_t br = desc[i];
        if (idx < 32) br ^= pgm_read_byte(&SCRAMBLE_B[idx]);
        raw[i] = bitReverse(br);
    }
    if (len < 5) return len;
    uint8_t tail = raw[len - 1];
    if (tail != 0xFF && tail != 0x55 && tail != 0xAA) return len;
    uint8_t start = len;
    for (int8_t i = len - 2; i >= 0; i--) {
        uint8_t diff = raw[i] ^ tail;
        uint8_t bits = 0;
        while (diff) { bits += diff & 1; diff >>= 1; }
        if (raw[i] == tail || bits <= 1) start = i;
        else break;
    }
    return start;
}

uint32_t crc32(const uint8_t *data, uint8_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t k = 0; k < 8; k++) {
            crc = (crc & 1) ? (crc >> 1) ^ 0xEDB88320 : crc >> 1;
        }
    }
    return ~crc;
}

// Fingerprint a descrambled packet: strip idle tail, CRC-32 the rest.
uint32_t extractButtonCode(const uint8_t *desc) {
    uint8_t n = findIdleStart(desc, 32);
    if (n == 0) return 0;
    return crc32(desc, n);
}

// ═══════════════════════════════════════════════════════════════════════
// Button-code storage (CRC-32 fingerprints in EEPROM)
// ═══════════════════════════════════════════════════════════════════════
const uint32_t EE_MAGIC = 0xCAFE1042UL;
uint32_t buttonHash[NUM_BUTTONS];     // 0 = not learned

void loadButtonCodes() {
    uint32_t magic;
    EEPROM.get(0, magic);
    if (magic != EE_MAGIC) {
        for (uint8_t i = 0; i < NUM_BUTTONS; i++) buttonHash[i] = 0;
        return;
    }
    for (uint8_t i = 0; i < NUM_BUTTONS; i++) {
        EEPROM.get(4 + i * 4, buttonHash[i]);
    }
}

void saveButtonCodes() {
    EEPROM.put(0, EE_MAGIC);
    for (uint8_t i = 0; i < NUM_BUTTONS; i++) {
        EEPROM.put(4 + i * 4, buttonHash[i]);
    }
}

int8_t buttonForHash(uint32_t hash) {
    if (hash == 0) return -1;
    for (uint8_t i = 0; i < NUM_BUTTONS; i++) {
        if (buttonHash[i] == hash) return i;
    }
    return -1;
}

// ═══════════════════════════════════════════════════════════════════════
// Controller state (was closure variables in main())
// ═══════════════════════════════════════════════════════════════════════
enum Mode {
    MODE_NONE, MODE_SOLID, MODE_DUAL,
    MODE_FADE, MODE_STROBE, MODE_CHASER, MODE_THEATER, MODE_TWINKLE, MODE_PRESET
};

Mode    activeMode   = MODE_NONE;
uint8_t dimmerIdx    = 9;            // 9 -> full brightness
Pixel   currentWrgb  = OFF;
bool    hasCurrent   = false;
uint8_t modesIdx     = 0;
uint8_t whiteIdx     = 2;           // Neutral White
bool    powerOn      = false;
uint8_t pendingSlot  = 0;           // 0 none, 1 color1, 2 color2
Pixel   dualColor1   = OFF, dualColor2 = OFF;
bool    hasDual1     = false, hasDual2 = false;

// Power-off snapshot for restore
struct Saved {
    bool    valid;
    Pixel   wrgb;  bool hasWrgb;
    Mode    mode;
    uint8_t dimmerIdx, modesIdx;
    Pixel   dual1, dual2; bool hasDual1, hasDual2;
};
Saved saved = {false};

// Timer
bool     timerActive = false;
uint32_t timerEnd    = 0;

// Debounce
uint32_t lastButtonHash = 0;
uint32_t lastButtonTime = 0;

// ── Animation working state ──
Pixel    fadeFrom, fadeTo;
uint32_t fadeStart;
bool     fadeHolding;
uint32_t fadeHoldStart;

Pixel    strobeColor;
uint32_t strobeLast;

Pixel    chaserColors[NUM_LEDS];
uint32_t chaserLast;

uint8_t  theaterOffset;
uint32_t theaterLast;

uint8_t  twPhase[NUM_LEDS];          // 0 delay, 1 fade_down, 2 fade_up, 3 hold
uint32_t twPhaseStart[NUM_LEDS];
uint16_t twDelay[NUM_LEDS];
Pixel    twCur[NUM_LEDS];            // current sequence color
Pixel    twNext[NUM_LEDS];           // target color while fading up
uint32_t twLastTick;

// ═══════════════════════════════════════════════════════════════════════
// Mode control
// ═══════════════════════════════════════════════════════════════════════
void stopMode() { activeMode = MODE_NONE; }

void startPreset(uint8_t idx) {
    Pixel pat[3] = {MODES_LIST[idx].p0, MODES_LIST[idx].p1, MODES_LIST[idx].p2};
    Pixel px[NUM_LEDS];
    for (uint8_t n = 0; n < NUM_LEDS; n++) px[n] = pat[n % 3];
    buildFrame(px);
    activeMode = MODE_PRESET;
}

void startFade(const Pixel *startColor) {
    fadeFrom = startColor ? *startColor : pickRandom(nullptr);
    fadeTo   = pickRandom(&fadeFrom);
    fadeStart = millis();
    fadeHolding = false;
    activeMode = MODE_FADE;
    buildSolid(fadeFrom, 255);
}

void startStrobe(const Pixel *startColor) {
    strobeColor = startColor ? *startColor : pickRandom(nullptr);
    strobeLast = millis();
    activeMode = MODE_STROBE;
    buildSolid(strobeColor, 255);
}

void startChaser() {
    for (uint8_t n = 0; n < NUM_LEDS; n++) chaserColors[n] = pickRandom(nullptr);
    chaserLast = millis();
    activeMode = MODE_CHASER;
    uint8_t dim = DIMMER_STEPS[dimmerIdx];
    Pixel px[NUM_LEDS];
    for (uint8_t n = 0; n < NUM_LEDS; n++) px[n] = dimPixel(chaserColors[n], dim);
    buildFrame(px);
}

void startTheater() {
    theaterOffset = 0;
    theaterLast = millis();
    activeMode = MODE_THEATER;
    Pixel px[NUM_LEDS];
    for (uint8_t n = 0; n < NUM_LEDS; n++) px[n] = (n % 3 == 0) ? NEUTRAL_WHITE : OFF;
    buildFrame(px);
}

void startTwinkle() {
    for (uint8_t n = 0; n < NUM_LEDS; n++) {
        twCur[n] = pickRandom(nullptr);
        twPhase[n] = 0;                      // delay
        twPhaseStart[n] = millis();
        twDelay[n] = random(1000, 4000);     // staggered start
    }
    twLastTick = millis();
    activeMode = MODE_TWINKLE;
    buildFrame(twCur);
}

void startModeEntry(uint8_t idx) {
    switch (MODES_LIST[idx].type) {
        case MT_PRESET:  startPreset(idx); break;
        case MT_CHASER:  startChaser();    break;
        case MT_THEATER: startTheater();   break;
        case MT_TWINKLE: startTwinkle();   break;
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Non-blocking animation update (called every loop)
// ═══════════════════════════════════════════════════════════════════════
void updateAnimation() {
    uint32_t now = millis();

    switch (activeMode) {
        case MODE_FADE: {
            if (!fadeHolding) {
                uint32_t elapsed = now - fadeStart;
                if (elapsed >= FADE_DURATION_MS) {
                    buildSolid(fadeTo, 255);
                    fadeHolding = true;
                    fadeHoldStart = now;
                } else {
                    uint16_t t = (uint16_t)((elapsed * 256UL) / FADE_DURATION_MS);
                    buildSolid(lerpPixel(fadeFrom, fadeTo, t), 255);
                }
            } else if (now - fadeHoldStart >= FADE_HOLD_MS) {
                fadeFrom = fadeTo;
                fadeTo = pickRandom(&fadeFrom);
                fadeStart = now;
                fadeHolding = false;
            }
            break;
        }
        case MODE_STROBE: {
            if (now - strobeLast >= STROBE_INTERVAL_MS) {
                strobeColor = pickRandom(&strobeColor);
                buildSolid(strobeColor, 255);
                strobeLast = now;
            }
            break;
        }
        case MODE_CHASER: {
            if (now - chaserLast >= CHASER_INTERVAL_MS) {
                for (uint8_t i = NUM_LEDS - 1; i > 0; i--) chaserColors[i] = chaserColors[i - 1];
                chaserColors[0] = pickRandom(nullptr);
                uint8_t dim = DIMMER_STEPS[dimmerIdx];
                Pixel px[NUM_LEDS];
                for (uint8_t n = 0; n < NUM_LEDS; n++) px[n] = dimPixel(chaserColors[n], dim);
                buildFrame(px);
                chaserLast = now;
            }
            break;
        }
        case MODE_THEATER: {
            if (now - theaterLast >= THEATER_INTERVAL_MS) {
                theaterOffset = (theaterOffset + 1) % 3;
                Pixel px[NUM_LEDS];
                for (uint8_t n = 0; n < NUM_LEDS; n++)
                    px[n] = (n % 3 == theaterOffset) ? NEUTRAL_WHITE : OFF;
                buildFrame(px);
                theaterLast = now;
            }
            break;
        }
        case MODE_TWINKLE: {
            if (now - twLastTick < TWINKLE_TICK_MS) break;
            twLastTick = now;
            Pixel px[NUM_LEDS];
            for (uint8_t n = 0; n < NUM_LEDS; n++) {
                uint32_t el = now - twPhaseStart[n];
                switch (twPhase[n]) {
                    case 0:  // delay — hold current color
                        px[n] = twCur[n];
                        if (el >= twDelay[n]) { twPhase[n] = 1; twPhaseStart[n] = now; }
                        break;
                    case 1: { // fade_down: curColor -> OFF
                        uint16_t t = el >= TWINKLE_FADE_DOWN_MS ? 256
                                     : (uint16_t)((el * 256UL) / TWINKLE_FADE_DOWN_MS);
                        px[n] = lerpPixel(twCur[n], OFF, t);
                        if (el >= TWINKLE_FADE_DOWN_MS) {
                            twNext[n] = pickRandom(&twCur[n]);
                            twPhase[n] = 2; twPhaseStart[n] = now;
                        }
                        break;
                    }
                    case 2: { // fade_up: OFF -> nextColor
                        uint16_t t = el >= TWINKLE_FADE_UP_MS ? 256
                                     : (uint16_t)((el * 256UL) / TWINKLE_FADE_UP_MS);
                        px[n] = lerpPixel(OFF, twNext[n], t);
                        if (el >= TWINKLE_FADE_UP_MS) {
                            twCur[n] = twNext[n];
                            px[n] = twCur[n];
                            twPhase[n] = 3; twPhaseStart[n] = now;
                        }
                        break;
                    }
                    case 3:  // hold
                        px[n] = twCur[n];
                        if (el >= TWINKLE_HOLD_MS) { twPhase[n] = 1; twPhaseStart[n] = now; }
                        break;
                }
            }
            buildFrame(px);
            break;
        }
        default: break;   // SOLID/DUAL/PRESET/NONE — static
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Timer (2-hour / 4-hour auto power-off)
// ═══════════════════════════════════════════════════════════════════════
void cancelTimer() { timerActive = false; }

void startTimer(uint8_t hours) {
    timerActive = true;
    timerEnd = millis() + (uint32_t)hours * 3600000UL;
}

void timerFire() {
    timerActive = false;
    if (!powerOn) return;
    saved.valid = true;
    saved.wrgb = currentWrgb; saved.hasWrgb = hasCurrent;
    saved.mode = activeMode;
    saved.dimmerIdx = dimmerIdx; saved.modesIdx = modesIdx;
    saved.dual1 = dualColor1; saved.dual2 = dualColor2;
    saved.hasDual1 = hasDual1; saved.hasDual2 = hasDual2;
    stopMode();
    hasCurrent = false;
    buildSolid(OFF, 255);
    powerOn = false;
    Serial.println(F("  Timer expired - Power: OFF"));
}

void checkTimer() {
    if (timerActive && (int32_t)(millis() - timerEnd) >= 0) timerFire();
}

// ═══════════════════════════════════════════════════════════════════════
// Button dispatch (port of handle_button)
// ═══════════════════════════════════════════════════════════════════════
void assignDualSlot(const Pixel &wrgb) {
    if (pendingSlot == 1) { dualColor1 = wrgb; hasDual1 = true; }
    else                  { dualColor2 = wrgb; hasDual2 = true; }
    pendingSlot = 0;
    stopMode();
    activeMode = MODE_DUAL;
    buildDual(hasDual1 ? dualColor1 : OFF, hasDual2 ? dualColor2 : OFF,
              DIMMER_STEPS[dimmerIdx]);
    Serial.println(F("  Dual color set"));
}

void handleButton(uint8_t id) {
    // ── Power (toggle with state save/restore) ──
    if (id == BTN_POWER) {
        if (powerOn) {
            saved.valid = true;
            saved.wrgb = currentWrgb; saved.hasWrgb = hasCurrent;
            saved.mode = activeMode;
            saved.dimmerIdx = dimmerIdx; saved.modesIdx = modesIdx;
            saved.dual1 = dualColor1; saved.dual2 = dualColor2;
            saved.hasDual1 = hasDual1; saved.hasDual2 = hasDual2;
            cancelTimer();
            stopMode();
            hasCurrent = false;
            buildSolid(OFF, 255);
            powerOn = false;
            Serial.println(F("  Power: OFF"));
        } else {
            powerOn = true;
            if (saved.valid) {
                dimmerIdx = saved.dimmerIdx;
                modesIdx  = saved.modesIdx;
                dualColor1 = saved.dual1; dualColor2 = saved.dual2;
                hasDual1 = saved.hasDual1; hasDual2 = saved.hasDual2;
                Mode m = saved.mode;
                if (m == MODE_DUAL) {
                    activeMode = MODE_DUAL;
                    buildDual(hasDual1 ? dualColor1 : OFF, hasDual2 ? dualColor2 : OFF,
                              DIMMER_STEPS[dimmerIdx]);
                    Serial.println(F("  Power: ON (dual)"));
                } else if (m == MODE_PRESET) {
                    startPreset(modesIdx);
                    Serial.println(F("  Power: ON (preset)"));
                } else if (m == MODE_FADE) {
                    startFade(saved.hasWrgb ? &saved.wrgb : nullptr);
                    Serial.println(F("  Power: ON (fade)"));
                } else if (m == MODE_STROBE) {
                    startStrobe(saved.hasWrgb ? &saved.wrgb : nullptr);
                    Serial.println(F("  Power: ON (strobe)"));
                } else if (m == MODE_CHASER) {
                    startChaser();  Serial.println(F("  Power: ON (chaser)"));
                } else if (m == MODE_THEATER) {
                    startTheater(); Serial.println(F("  Power: ON (theater)"));
                } else if (m == MODE_TWINKLE) {
                    startTwinkle(); Serial.println(F("  Power: ON (twinkle)"));
                } else {
                    currentWrgb = saved.hasWrgb ? saved.wrgb : NEUTRAL_WHITE;
                    hasCurrent = true; activeMode = MODE_SOLID;
                    buildSolid(currentWrgb, DIMMER_STEPS[dimmerIdx]);
                    Serial.println(F("  Power: ON"));
                }
            } else {
                currentWrgb = NEUTRAL_WHITE; hasCurrent = true; activeMode = MODE_SOLID;
                buildSolid(currentWrgb, DIMMER_STEPS[dimmerIdx]);
                Serial.println(F("  Power: ON (Neutral White)"));
            }
        }
        return;
    }

    if (!powerOn) return;

    // ── Color buttons (15 direct colors) ──
    Pixel wrgb;
    if (colorForButton(id, wrgb)) {
        if (pendingSlot) {
            assignDualSlot(wrgb);
        } else {
            stopMode();
            hasDual1 = hasDual2 = false;
            currentWrgb = wrgb; hasCurrent = true; activeMode = MODE_SOLID;
            buildSolid(currentWrgb, DIMMER_STEPS[dimmerIdx]);
            Serial.print(F("  -> ")); Serial.println(buttonName(id));
        }
        return;
    }

    // ── White Select (cycle temperatures) ──
    if (id == BTN_WHITE_SELECT) {
        whiteIdx = (whiteIdx + 1) % 5;
        Pixel w = WHITE_TEMPS[whiteIdx];
        if (pendingSlot) {
            assignDualSlot(w);
        } else {
            stopMode();
            hasDual1 = hasDual2 = false;
            currentWrgb = w; hasCurrent = true; activeMode = MODE_SOLID;
            buildSolid(currentWrgb, DIMMER_STEPS[dimmerIdx]);
            Serial.print(F("  White idx ")); Serial.println(whiteIdx);
        }
        return;
    }

    // ── Color 1 / Color 2 (dual-color slots) ──
    if (id == BTN_COLOR1) { pendingSlot = 1; Serial.println(F("  Color 1: pick a color")); return; }
    if (id == BTN_COLOR2) { pendingSlot = 2; Serial.println(F("  Color 2: pick a color")); return; }

    // ── Timers ──
    if (id == BTN_2HOUR) { startTimer(2); Serial.println(F("  Timer: 2-hour")); return; }
    if (id == BTN_4HOUR) { startTimer(4); Serial.println(F("  Timer: 4-hour")); return; }

    // ── Fade ──
    if (id == BTN_FADE) {
        dimmerIdx = 9;
        bool useCur = (activeMode == MODE_SOLID && hasCurrent);
        startFade(useCur ? &currentWrgb : nullptr);
        Serial.println(F("  Fade: ON"));
        return;
    }

    // ── Dimming (only solid / dual / chaser) ──
    if (id == BTN_DIMMING) {
        if (activeMode == MODE_FADE || activeMode == MODE_STROBE ||
            activeMode == MODE_THEATER || activeMode == MODE_TWINKLE ||
            activeMode == MODE_PRESET) return;
        dimmerIdx = (dimmerIdx == 0) ? 9 : dimmerIdx - 1;
        uint8_t dim = DIMMER_STEPS[dimmerIdx];
        if (activeMode == MODE_DUAL) {
            buildDual(hasDual1 ? dualColor1 : OFF, hasDual2 ? dualColor2 : OFF, dim);
        } else if (activeMode == MODE_SOLID) {
            buildSolid(currentWrgb, dim);
        }
        Serial.print(F("  Dimmer idx ")); Serial.println(dimmerIdx);
        return;
    }

    // ── Strobe ──
    if (id == BTN_STROBE) {
        dimmerIdx = 9;
        bool useCur = (activeMode == MODE_SOLID && hasCurrent);
        startStrobe(useCur ? &currentWrgb : nullptr);
        Serial.println(F("  Strobe: ON"));
        return;
    }

    // ── Modes (cycle holiday presets + animations) ──
    if (id == BTN_MODES) {
        dimmerIdx = 9;
        if (activeMode == MODE_PRESET || activeMode == MODE_CHASER ||
            activeMode == MODE_THEATER || activeMode == MODE_TWINKLE) {
            modesIdx = (modesIdx + 1) % 7;
        } else {
            modesIdx = 0;
        }
        startModeEntry(modesIdx);
        Serial.print(F("  Mode: ")); Serial.println(modeName(modesIdx));
        return;
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Learning mode (Serial-driven, replaces --learn / button_codes.json)
// ═══════════════════════════════════════════════════════════════════════
void flushSerial() { while (Serial.available()) Serial.read(); }

void learnButtons() {
    Serial.println(F("\n=== BUTTON LEARNING MODE ==="));
    Serial.println(F("For each button: hold it, then send any character."));

    for (uint8_t b = 0; b < NUM_BUTTONS; b++) {
        Serial.print(F("\nPress & HOLD [")); Serial.print(buttonName(b));
        Serial.println(F("], then send a character..."));
        flushSerial();
        while (!Serial.available()) { /* wait for go signal */ }
        flushSerial();
        Serial.println(F("  Capturing for 2 s..."));

        // Tally candidate fingerprints (small fixed table)
        uint32_t cand[8]; uint16_t cnt[8]; uint8_t nc = 0;
        uint32_t deadline = millis() + 2000;
        while ((int32_t)(millis() - deadline) < 0) {
            if (rfAvailable()) {
                uint8_t raw[32], desc[32];
                rfReadPayload(raw);
                descramble(raw, 32, desc);
                if (isBackgroundPacket(desc)) continue;
                uint32_t code = extractButtonCode(desc);
                if (code == 0) continue;
                bool found = false;
                for (uint8_t i = 0; i < nc; i++) {
                    if (cand[i] == code) { cnt[i]++; found = true; break; }
                }
                if (!found && nc < 8) { cand[nc] = code; cnt[nc] = 1; nc++; }
            }
        }
        if (nc == 0) { Serial.println(F("  No packets - skipped.")); continue; }
        uint8_t best = 0;
        for (uint8_t i = 1; i < nc; i++) if (cnt[i] > cnt[best]) best = i;
        buttonHash[b] = cand[best];
        Serial.print(F("  Learned 0x")); Serial.print(cand[best], HEX);
        Serial.print(F(" (")); Serial.print(cnt[best]); Serial.println(F(" hits)"));
    }

    saveButtonCodes();
    Serial.println(F("\nSaved button codes to EEPROM."));
}

void checkSerialCommand() {
    if (!Serial.available()) return;
    char c = Serial.read();
    if (c == 'L' || c == 'l') {
        learnButtons();
    } else if (c == 'C' || c == 'c') {
        for (uint8_t i = 0; i < NUM_BUTTONS; i++) buttonHash[i] = 0;
        saveButtonCodes();
        Serial.println(F("Cleared learned codes."));
    }
}

// ═══════════════════════════════════════════════════════════════════════
// setup() / loop()
// ═══════════════════════════════════════════════════════════════════════
void setup() {
    Serial.begin(115200);

    // Radio (software SPI) pins
    pinMode(PIN_RF_SCK, OUTPUT);  digitalWrite(PIN_RF_SCK, LOW);
    pinMode(PIN_RF_MOSI, OUTPUT); digitalWrite(PIN_RF_MOSI, LOW);
    pinMode(PIN_RF_MISO, INPUT);
    pinMode(PIN_RF_CSN, OUTPUT);  digitalWrite(PIN_RF_CSN, HIGH);
    pinMode(PIN_RF_CE, OUTPUT);   digitalWrite(PIN_RF_CE, LOW);

    // LEDs (hardware SPI). Leave the transaction open so MOSI idles HIGH
    // (last reset byte) between frames — the TM1815B latch/idle state.
    SPI.begin();
    SPI.beginTransaction(SPISettings(LED_SPI_HZ, MSBFIRST, SPI_MODE0));

    randomSeed(analogRead(A0) ^ micros());

    loadButtonCodes();
    rfConfigure();

    // Start dark
    buildSolid(OFF, 255);

    Serial.println(F("============================================"));
    Serial.println(F("  Enbrighten LED Controller (Nano Every)"));
    Serial.print(F("  Channel ")); Serial.print(RF_CHANNEL);
    Serial.print(F(" | LEDs ")); Serial.println(NUM_LEDS);
    uint8_t learned = 0;
    for (uint8_t i = 0; i < NUM_BUTTONS; i++) if (buttonHash[i]) learned++;
    Serial.print(F("  ")); Serial.print(learned);
    Serial.println(F(" button codes loaded."));
    Serial.println(F("  Send 'L' to learn, 'C' to clear."));
    Serial.println(F("============================================"));
}

void loop() {
    // 1. Poll the radio for a button packet
    if (rfAvailable()) {
        uint8_t raw[32], desc[32];
        rfReadPayload(raw);
        descramble(raw, 32, desc);

        if (!isBackgroundPacket(desc)) {
            uint32_t code = extractButtonCode(desc);
            uint32_t now = millis();
            bool dup = (code == lastButtonHash) && (now - lastButtonTime < DEBOUNCE_MS);
            if (!dup) {
                lastButtonHash = code;
                lastButtonTime = now;
                int8_t btn = buttonForHash(code);
                if (btn >= 0) handleButton(btn);
                else { Serial.print(F("  Unknown: 0x")); Serial.println(code, HEX); }
            }
        }
    }

    // 2. Advance the active animation (non-blocking)
    updateAnimation();

    // 3. Auto-off timer
    checkTimer();

    // 4. Continuous LED refresh
    showFrame();

    // 5. Serial commands (learn / clear)
    checkSerialCommand();
}
