// Local
#include "battery.h"

// System
#include <chrono>
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

// 40-pin I2C1 is /dev/i2c-7 on Jetson, then /dev/i2c-1
static const int BATTERY_BUS_PRIMARY = 7;
static const int BATTERY_BUS_FALLBACK = 1;

// INA219 default address and registers
static const int INA219_ADDRESS = 0x40;
static const int INA219_REGISTER_CONFIG = 0x00;
static const int INA219_REGISTER_BUS = 0x02;
static const int INA219_CONFIG_TOP_NIBBLE = 0x3000;
static const int INA219_BUS_MV_PER_BIT = 4;

// Read once a second and smooth the voltage
static const int BATTERY_CHECK_MS = 1000;
static const float BATTERY_FILTER = 0.99f;

// Open I2C file and last filtered reading
static int battery_file = -1;
static float battery_voltage_value = 0;
static string battery_label;
static bool battery_logged = false;
static steady_clock::time_point last_battery_check;
static bool last_battery_check_set = false;

// Later in this file
static void update_battery_reading();
static bool open_ina219();
static unsigned int read_ina219_register(int fileDescriptor, int registerAddress);
static bool is_ina219_config(unsigned int config);
static float read_bus_volts();

// Recheck the INA219 about once a second
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

// Empty when the meter is missing
string battery_text() {
    return battery_label;
}

// Filtered pack voltage, or 0 when unread
float battery_voltage() {
    return battery_voltage_value;
}

// Open the chip if needed, then filter a bus-voltage sample
static void update_battery_reading() {
    // Leave the label empty until the chip opens
    if (battery_file < 0 && !open_ina219()) {
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
    if (battery_voltage_value <= 0) battery_voltage_value = voltage;
    else battery_voltage_value = battery_voltage_value * BATTERY_FILTER + voltage * (1.0f - BATTERY_FILTER);

    // Face label
    char line[32];
    snprintf(line, sizeof(line), "%.1f V", battery_voltage_value);
    battery_label = line;

    // Log once
    if (!battery_logged) {
        printf("Battery INA219 %.2f V\n", battery_voltage_value);
        fflush(stdout);
        battery_logged = true;
    }
}

// Prefer the 40-pin I2C1 bus, /dev/i2c-7 on Jetson, then /dev/i2c-1
static bool open_ina219() {
#ifdef __linux__
    // Try the header bus first
    int buses[] = {BATTERY_BUS_PRIMARY, BATTERY_BUS_FALLBACK};
    for (int bus : buses) {
        // Open this /dev/i2c-N
        string path = "/dev/i2c-" + to_string(bus);
        int fileDescriptor = open(path.c_str(), O_RDWR);
        if (fileDescriptor < 0) continue;

        // Skip the onboard INA3221
        unsigned int config = read_ina219_register(fileDescriptor, INA219_REGISTER_CONFIG);
        if (!is_ina219_config(config)) {
            close(fileDescriptor);
            continue;
        }

        // Keep this handle
        battery_file = fileDescriptor;
        printf("Battery INA219 on %s address 0x%02x\n", path.c_str(), INA219_ADDRESS);
        fflush(stdout);
        return true;
    }
#endif
    return false;
}

#ifdef __linux__
// Read a 16-bit INA219 register, high byte first
static unsigned int read_ina219_register(int fileDescriptor, int registerAddress) {
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
    if (ioctl(fileDescriptor, I2C_RDWR, &packet) < 0) return 0;
    return ((unsigned int)data[0] << 8) | data[1];
}
#endif

#ifndef __linux__
// No I2C on this build
static unsigned int read_ina219_register(int fileDescriptor, int registerAddress) {
    (void)fileDescriptor;
    (void)registerAddress;
    return 0;
}
#endif

// INA219 reset config is 0x399F, onboard INA3221 on i2c-1 is 0x7xxx
static bool is_ina219_config(unsigned int config) {
    return (config & 0xF000) == INA219_CONFIG_TOP_NIBBLE;
}

// Bus voltage register, 4 mV per bit, ignore ready and overflow flags
static float read_bus_volts() {
    // Need an open chip
    if (battery_file < 0) return 0;

    // Convert the 4 mV bits
    unsigned int raw = read_ina219_register(battery_file, INA219_REGISTER_BUS);
    if (raw == 0) return 0;
    int milliVolts = (int)(raw >> 3) * INA219_BUS_MV_PER_BIT;
    return milliVolts / 1000.0f;
}
