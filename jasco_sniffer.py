import spidev
import time
import sys
from gpiozero import OutputDevice

# --- Configuration & Hardware Setup ---
CE_PIN = 22 # GPIO 22 (Pin 15 on the Pi header)
HOP_CHANNELS = [21, 42, 64] # The frequencies from the FCC report
HOP_DELAY = 0.05 # How long to listen on each channel (in seconds)

# Initialize CE pin
ce = OutputDevice(CE_PIN)
ce.off() # Turn off radio listening initially

# Initialize SPI
spi = spidev.SpiDev()
spi.open(0, 0) # SPI bus 0, Device 0 (CSN is GPIO 8)
spi.max_speed_hz = 1000000 # 1 MHz is stable for nRF24
spi.mode = 0b00

# --- nRF24L01+ Registers & Commands ---
R_REGISTER    = 0x00
W_REGISTER    = 0x20
R_RX_PAYLOAD  = 0x61
FLUSH_RX      = 0xE2
NOP           = 0xFF

REG_CONFIG    = 0x00
REG_EN_AA     = 0x01
REG_EN_RXADDR = 0x02
REG_SETUP_AW  = 0x03
REG_RF_CH     = 0x05
REG_RF_SETUP  = 0x06
REG_STATUS    = 0x07
REG_RX_ADDR_P0= 0x0A
REG_RX_PW_P0  = 0x11

def write_reg(reg, value):
    """Write a single byte to a register."""
    spi.xfer2([W_REGISTER | reg, value])

def write_reg_multi(reg, values):
    """Write multiple bytes to a register (e.g., for MAC addresses)."""
    spi.xfer2([W_REGISTER | reg] + values)

def read_reg(reg):
    """Read a single byte from a register."""
    return spi.xfer2([R_REGISTER | reg, 0xFF])[1]

def setup_promiscuous_radio():
    """Configures the nRF24L01+ to listen to raw GFSK packets and verifies CRC is disabled."""
    print("Initializing nRF24L01+...")
    ce.off() # Must be off to configure
    
    # 1. Disable Auto-Ack (Mandatory to disable CRC)
    write_reg(REG_EN_AA, 0x00)
    
    # 2. Power UP (PWR_UP=1), Set as Receiver (PRX=1), Disable CRC (EN_CRC=0)
    # Binary 0x03 = 0b00000011. Bit 3 (CRC) is set to 0.
    write_reg(REG_CONFIG, 0x03)
    
    # --- CRC VERIFICATION CHECK ---
    time.sleep(0.01) # Give the chip a millisecond to apply settings
    verify_config = read_reg(REG_CONFIG)
    verify_aa = read_reg(REG_EN_AA)
    
    if (verify_config & 0x08) == 0 and verify_aa == 0x00:
        print("[SUCCESS] Auto-Ack and CRC are successfully disabled.")
    else:
        print(f"[ERROR] Failed to disable CRC. (CONFIG: {bin(verify_config)}, EN_AA: {bin(verify_aa)})")
        print("Check your wiring to the nRF24L01+ module.")
        sys.exit(1) # Halt the program if hardware isn't responding correctly
    # ------------------------------

    # 3. Set Address Width to minimum (3 bytes is the standard minimum)
    write_reg(REG_SETUP_AW, 0x01) 
    
    # 4. Set Data Rate to 1 Mbps, TX Power to 0dBm (from FCC report bandwidth)
    write_reg(REG_RF_SETUP, 0x07)
    
    # 5. Enable Pipe 0
    write_reg(REG_EN_RXADDR, 0x01)
    
    # 6. Set a generic dummy listening address (preamble catcher)
    write_reg_multi(REG_RX_ADDR_P0, [0x55, 0x55, 0x55])
    
    # 7. Set Payload size to 32 bytes (capture maximum packet size)
    write_reg(REG_RX_PW_P0, 32)
    
    # 8. Flush the RX buffer of any old garbage
    spi.xfer2([FLUSH_RX])
    
    # Clear any pending interrupts
    write_reg(REG_STATUS, 0x70)
    print("[SUCCESS] Radio configured for Promiscuous Sniffing.")

def read_payload():
    """Pulls the 32-byte payload out of the nRF buffer."""
    # Send the read command, followed by 32 dummy bytes to clock the data out
    response = spi.xfer2([R_RX_PAYLOAD] + [0xFF] * 32)
    return response[1:] # Drop the first byte (which is just the status register)

# --- Main Sniffing Loop ---
try:
    print("=======================================")
    print("   Jasco / Beken 2.4GHz RF Sniffer")
    print("=======================================")
    setup_promiscuous_radio()
    
    print(f"\nSniffer running! Hopping between FCC Channels: {HOP_CHANNELS}")
    print("Press CTRL+C to stop.\n")
    
    channel_index = 0

    while True:
        # Change Channel
        current_ch = HOP_CHANNELS[channel_index]
        ce.off() # Must pause radio to change channel
        write_reg(REG_RF_CH, current_ch)
        ce.on()  # Resume listening
        
        # Listen for a short burst
        time.sleep(HOP_DELAY)
        
        # Ask the chip for its STATUS register using a No-Operation command
        status = spi.xfer2([NOP])[0]
        
        # Check Bit 6 of STATUS (RX_DR - Data Ready)
        if status & 0x40:
            payload = read_payload()
            
            # Format as Hex string for easy reading
            hex_payload = " ".join([f"{b:02X}" for b in payload])
            
            print(f"[CH {current_ch:02d}] Packet: {hex_payload}")
            
            # Clear the interrupt so the chip can receive the next packet
            write_reg(REG_STATUS, 0x40) 
            
        # Move to next channel
        channel_index = (channel_index + 1) % len(HOP_CHANNELS)

except KeyboardInterrupt:
    print("\nStopping sniffer...")
finally:
    ce.off()
    spi.close()