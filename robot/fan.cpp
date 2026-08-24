#include "fan.h"
#include <fstream>
#include <filesystem>
#include <system_error>
#include <chrono>
#include <cstdio>
using namespace std;
using namespace std::chrono;

static const char* HWMON_ROOT = "/sys/class/hwmon";
static const char* THERMAL_ROOT = "/sys/class/thermal";
static const char* FAN_PWM_NAME = "pwmfan";
static const char* FAN_TACH_NAME = "pwm_tach";
static const char* CPU_THERMAL_NAME = "cpu-thermal";
static const char* JUNCTION_THERMAL_NAME = "tj-thermal";
static const int FAN_DUTY_MAX = 255;
static const int FAN_RPM_AT_FULL = 6000;
static const int FAN_SLOW_DIVISOR = 2;
static const int FAN_MIN_EXPECTED_RPM = 500;
static const int FAN_CHECK_MS = 1000;
static const int FAN_SLOW_HITS_NEEDED = 3;
static const int TEMPERATURE_WARN_C = 90;
static const int TEMPERATURE_HITS_NEEDED = 3;
static const int FAN_READ_FAILED = -1;
static const int TEMPERATURE_MILLI_PER_C = 1000;

static string fan_duty_path;
static string fan_rpm_path;
static string temperature_path;
static bool fan_paths_ready = false;
static int fan_slow_hits = 0;
static int temperature_hot_hits = 0;
static string fan_warning;
static string temperature_warning;
static steady_clock::time_point last_fan_check;
static bool last_fan_check_set = false;

static void find_fan_paths();
static void find_temperature_path();
static void update_fan_warning();
static void update_temperature_warning();
static int read_hwmon_int(const string& path);

// Recheck fan and CPU temperature about once a second
void check_fan() {
#ifdef __linux__
    auto now = steady_clock::now();
    if (last_fan_check_set && duration_cast<milliseconds>(now - last_fan_check).count() < FAN_CHECK_MS) {
        return;
    }
    last_fan_check = now;
    last_fan_check_set = true;
    update_fan_warning();
    update_temperature_warning();
#endif
}

// Empty when the fan is keeping up
string fan_warning_text() {
    return fan_warning;
}

// Empty when CPU is at or below 90 C
string temperature_warning_text() {
    return temperature_warning;
}

// Read PWM and tach, warn when RPM is under half of the PWM target
static void update_fan_warning() {
    if (!fan_paths_ready) {
        find_fan_paths();
        fan_paths_ready = true;
    }
    if (fan_duty_path.empty() || fan_rpm_path.empty()) {
        return;
    }

    // Expected RPM scales with PWM duty, full duty is 6000 on this board
    int fan_duty = read_hwmon_int(fan_duty_path);
    int fan_rpm = read_hwmon_int(fan_rpm_path);
    if (fan_duty == FAN_READ_FAILED || fan_rpm == FAN_READ_FAILED) {
        return;
    }
    int expected_rpm = fan_duty * FAN_RPM_AT_FULL / FAN_DUTY_MAX;
    if (expected_rpm < FAN_MIN_EXPECTED_RPM) {
        fan_slow_hits = 0;
        fan_warning.clear();
        return;
    }

    // Require a few slow samples, tach can glitch for a second
    bool slow = fan_rpm < expected_rpm / FAN_SLOW_DIVISOR;
    if (slow) {
        fan_slow_hits += 1;
    } else {
        fan_slow_hits = 0;
        fan_warning.clear();
        return;
    }
    if (fan_slow_hits < FAN_SLOW_HITS_NEEDED) {
        return;
    }

    // Keep the on-screen line, print once when it first trips
    string warning = "Fan slow: " + to_string(fan_rpm) + " RPM, expected " + to_string(expected_rpm);
    if (fan_warning.empty()) {
        printf("%s\n", warning.c_str());
        fflush(stdout);
    }
    fan_warning = warning;
}

// Warn when CPU thermal stays above 90 C
static void update_temperature_warning() {
    if (temperature_path.empty()) {
        find_temperature_path();
    }
    if (temperature_path.empty()) {
        return;
    }

    int temperature_milli = read_hwmon_int(temperature_path);
    if (temperature_milli == FAN_READ_FAILED) {
        return;
    }
    int temperature_c = temperature_milli / TEMPERATURE_MILLI_PER_C;
    if (temperature_c <= TEMPERATURE_WARN_C) {
        temperature_hot_hits = 0;
        temperature_warning.clear();
        return;
    }

    // Require a few hot samples
    temperature_hot_hits += 1;
    if (temperature_hot_hits < TEMPERATURE_HITS_NEEDED) {
        return;
    }

    // Keep the on-screen line, print once when it first trips
    string warning = "Hot: " + to_string(temperature_c) + " C";
    if (temperature_warning.empty()) {
        printf("%s\n", warning.c_str());
        fflush(stdout);
    }
    temperature_warning = warning;
}

// Match hwmon devices by name so the hwmon index can change
static void find_fan_paths() {
    error_code error;
    if (!filesystem::exists(HWMON_ROOT, error)) {
        return;
    }
    for (const auto& entry : filesystem::directory_iterator(HWMON_ROOT, error)) {
        ifstream name_file(entry.path() / "name");
        string name;
        if (!(name_file >> name)) {
            continue;
        }
        if (name == FAN_PWM_NAME) {
            fan_duty_path = (entry.path() / "pwm1").string();
        }
        if (name == FAN_TACH_NAME) {
            fan_rpm_path = (entry.path() / "rpm").string();
        }
    }
}

// Prefer CPU thermal, else the junction zone
static void find_temperature_path() {
    error_code error;
    if (!filesystem::exists(THERMAL_ROOT, error)) {
        return;
    }
    string junction_path;
    for (const auto& entry : filesystem::directory_iterator(THERMAL_ROOT, error)) {
        ifstream type_file(entry.path() / "type");
        string type;
        if (!(type_file >> type)) {
            continue;
        }
        if (type == CPU_THERMAL_NAME) {
            temperature_path = (entry.path() / "temp").string();
            return;
        }
        if (type == JUNCTION_THERMAL_NAME) {
            junction_path = (entry.path() / "temp").string();
        }
    }
    temperature_path = junction_path;
}

// Read one integer from a sysfs file, or -1
static int read_hwmon_int(const string& path) {
    ifstream in(path);
    int value = FAN_READ_FAILED;
    if (in >> value) {
        return value;
    }
    return FAN_READ_FAILED;
}
