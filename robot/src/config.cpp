// Local
#include "config.h"

// JSON
#include "json.hpp"

// System
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <system_error>

// Namespace
using namespace std;
using json = nlohmann::ordered_json;

// Settings sit next to the robot sources, one directory above the binary
static const char* CONFIG_NAME = "config.json";
static const int CONFIG_INDENT = 2;

// One lock for load and save so they cannot interleave
static recursive_mutex config_mutex;

// Later in this file
static string config_file_path();
static bool read_travel_limit(const json& settings, const char* name, const char* previous_name, int& value);

// Load ./config.json, servo motion needs all six travel keys
AppConfig loadConfig() {
    lock_guard<recursive_mutex> lock(config_mutex);

    // Start from the defaults, then open the file
    AppConfig config;
    string path = config_file_path();
    ifstream input(path);

    // Create config.json from the defaults, including servo travel
    if (!input.good()) {
        saveConfig(config);
        cout << "Created " << path << " from defaults" << endl;
        return config;
    }

    // Keep the default for anything the file leaves out
    try {
        json settings;
        input >> settings;
        if (settings.contains("useCamera"))
            config.useCamera = settings["useCamera"].get<bool>();
        if (settings.contains("faceTracking"))
            config.faceTracking = settings["faceTracking"].get<bool>();

        // Use servo limits only when every axis min and max is present
        bool has_pan_min = read_travel_limit(settings, "pan_min", "min_x", config.pan_min);
        bool has_pan_max = read_travel_limit(settings, "pan_max", "max_x", config.pan_max);
        bool has_tilt_min = read_travel_limit(settings, "tilt_min", "min_y", config.tilt_min);
        bool has_tilt_max = read_travel_limit(settings, "tilt_max", "max_y", config.tilt_max);
        bool has_hat_min = read_travel_limit(settings, "hat_min", "min_hat", config.hat_min);
        bool has_hat_max = read_travel_limit(settings, "hat_max", "max_hat", config.hat_max);
        config.has_servo_limits = has_pan_min && has_pan_max && has_tilt_min && has_tilt_max && has_hat_min && has_hat_max;

        // Hat horn wired the other way, only -1 flips it, anything else stays 1
        if (settings.contains("hat_dir")) {
            config.hat_dir = settings["hat_dir"].get<int>();
            if (config.hat_dir != -1)
                config.hat_dir = 1;
        }

    // Hold the servos still when the file cannot be parsed
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
        config.loaded = false;
        config.has_servo_limits = false;
    }

    // Hand back the merged settings
    return config;
}

// Write through a temp file so a crash cannot leave config.json empty
void saveConfig(const AppConfig& config) {
    lock_guard<recursive_mutex> lock(config_mutex);

    // Collect the camera settings
    json settings;
    settings["useCamera"] = config.useCamera;
    settings["faceTracking"] = config.faceTracking;

    // Keep existing travel limits, do not invent defaults
    if (config.has_servo_limits) {
        settings["pan_min"] = config.pan_min;
        settings["pan_max"] = config.pan_max;
        settings["tilt_min"] = config.tilt_min;
        settings["tilt_max"] = config.tilt_max;
        settings["hat_min"] = config.hat_min;
        settings["hat_max"] = config.hat_max;
        settings["hat_dir"] = config.hat_dir;
    }

    // Open the temp file next to the real one
    string path = config_file_path();
    string temp_path = path + ".tmp";
    ofstream output(temp_path);
    if (!output.good()) {
        cerr << "Failed to write config.json" << endl;
        return;
    }

    // Write the settings out
    output << settings.dump(CONFIG_INDENT) << '\n';
    if (!output.good()) {
        cerr << "Failed to write config.json" << endl;
        return;
    }

    // Close it, then swap the temp file over config.json
    output.close();
    if (rename(temp_path.c_str(), path.c_str()) != 0) {
        cerr << "Failed to replace config.json" << endl;
    }
}

// Path of config.json beside the sources, not inside build
static string config_file_path() {
    error_code error;
    filesystem::path executable = filesystem::read_symlink("/proc/self/exe", error);
    if (error) {
        return CONFIG_NAME;
    }
    return (executable.parent_path().parent_path() / CONFIG_NAME).string();
}

// Read a travel limit, new name first then the previous config.json key
static bool read_travel_limit(const json& settings, const char* name, const char* previous_name, int& value) {
    // Prefer the current key
    if (settings.contains(name)) {
        value = settings[name].get<int>();
        return true;
    }

    // Fall back to the older key so old config files still work
    if (settings.contains(previous_name)) {
        value = settings[previous_name].get<int>();
        return true;
    }

    // Missing on both names
    return false;
}
