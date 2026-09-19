import spidev
import time

# ============================================================
# MCP3008 CONFIGURATION
# ============================================================

SPI_BUS = 0
SPI_DEVICE = 0       # CE0

CHANNEL = 0          # Soil moisture connected to CH0

ADC_MAX = 1023
VREF = 3.3

# ============================================================
# SOIL MOISTURE CALIBRATION
# ============================================================

DRY_ADC = 500
WET_ADC = 50

# ============================================================
# SPI SETUP
# ============================================================

spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEVICE)

spi.max_speed_hz = 1350000
spi.mode = 0


# ============================================================
# READ MCP3008
# ============================================================

def read_adc(channel):

    if channel < 0 or channel > 7:
        raise ValueError("Channel must be between 0 and 7")

    result = spi.xfer2([
        1,
        (8 + channel) << 4,
        0
    ])

    value = ((result[1] & 3) << 8) | result[2]

    return value


# ============================================================
# AVERAGE MULTIPLE READINGS
# ============================================================

def read_average(channel, samples=20):

    readings = []

    for _ in range(samples):
        readings.append(read_adc(channel))
        time.sleep(0.01)

    return sum(readings) / len(readings)


# ============================================================
# CONVERT ADC TO VOLTAGE
# ============================================================

def adc_to_voltage(adc):

    return adc * VREF / ADC_MAX


# ============================================================
# CONVERT ADC TO MOISTURE %
# ============================================================

def adc_to_moisture(adc):

    moisture = (
        (DRY_ADC - adc)
        / (DRY_ADC - WET_ADC)
    ) * 100

    # Limit to 0–100%
    moisture = max(0, min(100, moisture))

    return moisture


# ============================================================
# MAIN
# ============================================================

try:

    while True:

        adc = read_average(CHANNEL)

        voltage = adc_to_voltage(adc)

        moisture = adc_to_moisture(adc)

        print("--------------------------------")
        print(f"ADC:       {adc:.1f}")
        print(f"Voltage:   {voltage:.3f} V")
        print(f"Moisture:  {moisture:.1f}%")

        time.sleep(1)


except KeyboardInterrupt:

    print("\nStopping...")


finally:

    spi.close()