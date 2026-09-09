// Local
#include "battery.h"

// System
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fcntl.h>
#include <string>
#include <unistd.h>

// Linux I2C
#ifdef __linux__
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <sys/ioctl.h>
#endif

// Namespace
using namespace std;
using namespace std::chrono;

// The 40-pin I2C1 header bus on Jetson, then the older bus number
static const int BATTERY_BUS_PRIMARY = 7;
static const int BATTERY_BUS_FALLBACK = 1;

// INA219 default address and registers
static const int INA219_ADDRESS = 0x40;
static const int INA219_REGISTER_CONFIG = 0x00;
static const int INA219_REGISTER_SHUNT = 0x01;
static const int INA219_REGISTER_BUS = 0x02;
static const int INA219_CONFIG_TOP_MASK = 0xF000;
static const int INA219_CONFIG_TOP_NIBBLE = 0x3000;
static const int INA219_BUS_MV_PER_BIT = 4;
static const int INA219_SHUNT_UV_PER_BIT = 10;

// Typical INA219 module shunt
static const float BATTERY_SHUNT_OHMS = 0.1f;

// How often to read the meter, and how hard to smooth each reading
static const int BATTERY_CHECK_MS = 1000;
static const float BATTERY_FILTER = 0.99f;
static const float BATTERY_CURRENT_FILTER = 0.7f;

// Room for the face label text
static const int BATTERY_LABEL_SIZE = 48;

// Empty and full pack voltage for a 3S LiPo
static const float BATTERY_VOLTAGE_EMPTY = 9.0f;
static const float BATTERY_VOLTAGE_FULL = 12.6f;

// A 24V pack when the reading runs above the detect point, with its own ends
static const float BATTERY_VOLTAGE_24V_DETECT = 15.0f;
static const float BATTERY_VOLTAGE_24V_EMPTY = 19.0f;
static const float BATTERY_VOLTAGE_24V_FULL = 25.0f;

// Shape of the LiPo S-curve, the midpoint sits above the middle of the span
static const float BATTERY_CURVE_STEEPNESS = 8.0f;
static const float BATTERY_CURVE_MID = 0.70f;

// Open I2C file and last filtered reading
static int battery_file = -1;
static float battery_voltage_value = 0;
static int battery_percent_value = 0;
static float battery_current_value = 0;
static bool battery_current_ready = false;
static string battery_label;
static bool battery_logged = false;
static steady_clock::time_point last_battery_check;
static bool last_battery_check_set = false;

// Later in this file
static void update_battery_reading();
static bool open_INA219();
static unsigned int read_INA219_register(int fileDescriptor, int registerAddress);
static bool is_INA219_config(unsigned int config);
static float read_bus_volts();
static int percent_from_voltage(float packVolts);
static float read_shunt_amps();

// Recheck the INA219 battery voltage and current
void check_battery() {
#ifdef __linux__
    // Skip until a second has passed
    auto now = steady_clock::now();
    if (last_battery_check_set && duration_cast<milliseconds>(now - last_battery_check).count() < BATTERY_CHECK_MS) {
        return;
    }

    // Sample the meter
    last_battery_check = now;
    last_battery_check_set = true;
    update_battery_reading();
#endif
}

// Open the chip if needed, then filter a bus-voltage sample
static void update_battery_reading() {
    // Leave the label empty until the chip opens
    if (battery_file < 0 && !open_INA219()) {
        battery_label.clear();
        return;
    }

    // Drop a bad sample
    float voltage = read_bus_volts();
    if (voltage <= 0) {
        battery_label.clear();
        return;
    }

    // Smooth after the first reading
    if (battery_voltage_value <= 0)
        battery_voltage_value = voltage;
    else
        battery_voltage_value = battery_voltage_value * BATTERY_FILTER + voltage * (1.0f - BATTERY_FILTER);

    // Percent from the pack curve, clamped to empty and full
    battery_percent_value = percent_from_voltage(battery_voltage_value);

    // Smooth shunt current, 0 A is a real reading
    float amps = read_shunt_amps();
    if (!battery_current_ready) {
        battery_current_value = amps;
        battery_current_ready = true;
    } else {
        battery_current_value = battery_current_value * BATTERY_CURRENT_FILTER + amps * (1.0f - BATTERY_CURRENT_FILTER);
    }

    // Build the face label
    char line[BATTERY_LABEL_SIZE];
    snprintf(line, sizeof(line), "Battery: %d%%, %.2f V, %.2f A", battery_percent_value, battery_voltage_value, battery_current_value);
    battery_label = line;

    // Log the first good reading once
    if (!battery_logged) {
        printf("Battery: %d%%, %.2f V, %.2f A\n", battery_percent_value, battery_voltage_value, battery_current_value);
        fflush(stdout);
        battery_logged = true;
    }
}

// Prefer the 40-pin I2C1 header bus, then fall back to the older bus number
static bool open_INA219() {
#ifdef __linux__
    // Try the header bus first
    int buses[] = {BATTERY_BUS_PRIMARY, BATTERY_BUS_FALLBACK};
    for (int bus : buses) {
        // Open this /dev/i2c-N
        string path = "/dev/i2c-" + to_string(bus);
        int fileDescriptor = open(path.c_str(), O_RDWR);
        if (fileDescriptor < 0)
            continue;

        // Skip the onboard INA3221
        unsigned int config = read_INA219_register(fileDescriptor, INA219_REGISTER_CONFIG);
        if (!is_INA219_config(config)) {
            close(fileDescriptor);
            continue;
        }

        // Keep this handle
        battery_file = fileDescriptor;
        return true;
    }
#endif

    // No INA219 on either bus
    return false;
}

#ifdef __linux__
// Read a 16-bit INA219 register, high byte first
static unsigned int read_INA219_register(int fileDescriptor, int registerAddress) {
    // Write the register, then read two bytes
    unsigned char command = (unsigned char)registerAddress;
    unsigned char data[2] = {0, 0};
    struct i2c_msg messages[2];
    messages[0].addr = INA219_ADDRESS;
    messages[0].flags = 0;
    messages[0].len = 1;
    messages[0].buf = &command;
    messages[1].addr = INA219_ADDRESS;
    messages[1].flags = I2C_M_RD;
    messages[1].len = 2;
    messages[1].buf = data;

    // One combined I2C transaction
    struct i2c_rdwr_ioctl_data packet;
    packet.msgs = messages;
    packet.nmsgs = 2;
    if (ioctl(fileDescriptor, I2C_RDWR, &packet) < 0)
        return 0;

    // Join the two bytes
    return ((unsigned int)data[0] << 8) | data[1];
}
#endif

#ifndef __linux__
// No I2C on this build
static unsigned int read_INA219_register(int fileDescriptor, int registerAddress) {
    (void)fileDescriptor;
    (void)registerAddress;
    return 0;
}
#endif

// Tell an INA219 from the onboard INA3221 by the top nibble of its config
static bool is_INA219_config(unsigned int config) {
    return (config & INA219_CONFIG_TOP_MASK) == INA219_CONFIG_TOP_NIBBLE;
}

// Bus voltage register, ignore the ready and overflow flags in the low bits
static float read_bus_volts() {
    // Need an open chip
    if (battery_file < 0)
        return 0;

    // Shift the flags off, then scale the bits to volts
    unsigned int raw = read_INA219_register(battery_file, INA219_REGISTER_BUS);
    if (raw == 0)
        return 0;
    int milliVolts = (int)(raw >> 3) * INA219_BUS_MV_PER_BIT;
    return milliVolts / 1000.0f;
}

// Turn pack volts into percent, an S-curve for 3S LiPo and linear for 24V
static int percent_from_voltage(float packVolts) {
    // A 24V pack runs linear between its own two ends
    if (packVolts > BATTERY_VOLTAGE_24V_DETECT) {
        if (packVolts <= BATTERY_VOLTAGE_24V_EMPTY)
            return 0;
        if (packVolts >= BATTERY_VOLTAGE_24V_FULL)
            return 100;
        float span = BATTERY_VOLTAGE_24V_FULL - BATTERY_VOLTAGE_24V_EMPTY;
        int percent = (int)(100.0f * (packVolts - BATTERY_VOLTAGE_24V_EMPTY) / span + 0.5f);
        if (percent < 0)
            percent = 0;
        if (percent > 100)
            percent = 100;
        return percent;
    }

    // Hard stop at empty and full pack volts
    if (packVolts <= BATTERY_VOLTAGE_EMPTY)
        return 0;
    if (packVolts >= BATTERY_VOLTAGE_FULL)
        return 100;

    // Map voltage onto the span, then a logistic so the middle stays flatter than linear
    float span = BATTERY_VOLTAGE_FULL - BATTERY_VOLTAGE_EMPTY;
    float fraction = (packVolts - BATTERY_VOLTAGE_EMPTY) / span;
    float sigmoid = 1.0f / (1.0f + expf(-BATTERY_CURVE_STEEPNESS * (fraction - BATTERY_CURVE_MID)));
    float sigmoidEmpty = 1.0f / (1.0f + expf(-BATTERY_CURVE_STEEPNESS * (0.0f - BATTERY_CURVE_MID)));
    float sigmoidFull = 1.0f / (1.0f + expf(-BATTERY_CURVE_STEEPNESS * (1.0f - BATTERY_CURVE_MID)));

    // Rescale the curve so empty lands on 0 and full lands on 100
    int percent = (int)(100.0f * (sigmoid - sigmoidEmpty) / (sigmoidFull - sigmoidEmpty) + 0.5f);
    if (percent < 0)
        percent = 0;
    if (percent > 100)
        percent = 100;
    return percent;
}

// Shunt voltage over the sense resistor, signed
static float read_shunt_amps() {
    // Need an open chip
    if (battery_file < 0)
        return 0;

    // Scale the signed bits to volts, then divide by the shunt
    unsigned int raw = read_INA219_register(battery_file, INA219_REGISTER_SHUNT);
    int16_t signedRaw = (int16_t)raw;
    float shuntVolts = signedRaw * (INA219_SHUNT_UV_PER_BIT / 1000000.0f);
    return shuntVolts / BATTERY_SHUNT_OHMS;
}

// Return the face label, empty when the meter is missing
string battery_text() {
    return battery_label;
}

// Return the filtered pack voltage, 0 when unread
float battery_voltage() {
    return battery_voltage_value;
}

// Return charge from 0 to 100 on the pack curve, 0 when unread
int battery_percent() {
    return battery_percent_value;
}

// Return the filtered shunt current in amps, sign follows the meter wiring
float battery_current() {
    return battery_current_value;
}
