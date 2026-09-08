// Local
#include "config.h"

// JSON
#include "json.hpp"

// System
#include <cstdio>
#include <fstream>
#include <iostream>
#include <mutex>

// Namespace
using namespace std;
using json = nlohmann::ordered_json;

// Settings sit next to the binary, in the working directory
static const char* CONFIG_PATH = "config.json";

// One lock for load and save so they cannot interleave
static recursive_mutex config_mutex;

// Read a travel limit
static bool read_travel_limit(const json& settings, const char* name, const char* previous_name, int& value);

// Load ./config.json, servo motion needs all six travel keys
AppConfig loadConfig() {
    lock_guard<recursive_mutex> lock(config_mutex);
    AppConfig config;
    ifstream input(CONFIG_PATH);
    if (!input.good()) {

        // Create config.json from AppConfig defaults, including servo travel
        saveConfig(config);
        cout << "Created " << CONFIG_PATH << " from defaults" << endl;
        return config;
    }

    // Keep the default for anything the file leaves out
    try {
        json settings;
        input >> settings;
        if (settings.contains("useCamera")) config.useCamera = settings["useCamera"].get<bool>();
        if (settings.contains("faceTracking")) config.faceTracking = settings["faceTracking"].get<bool>();

        // Use servo limits only when every axis min and max is present
        bool has_pan_min = read_travel_limit(settings, "pan_min", "min_x", config.pan_min);
        bool has_pan_max = read_travel_limit(settings, "pan_max", "max_x", config.pan_max);
        bool has_tilt_min = read_travel_limit(settings, "tilt_min", "min_y", config.tilt_min);
        bool has_tilt_max = read_travel_limit(settings, "tilt_max", "max_y", config.tilt_max);
        bool has_hat_min = read_travel_limit(settings, "hat_min", "min_hat", config.hat_min);
        bool has_hat_max = read_travel_limit(settings, "hat_max", "max_hat", config.hat_max);
        config.has_servo_limits = has_pan_min && has_pan_max && has_tilt_min && has_tilt_max && has_hat_min && has_hat_max;
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
        config.loaded = false;
        config.has_servo_limits = false;
    }
    return config;
}

// Write through a temp file so a crash cannot leave config.json empty
void saveConfig(const AppConfig& config) {
    lock_guard<recursive_mutex> lock(config_mutex);
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
    }

    // Write the temp file first, then replace config.json
    ofstream output("config.json.tmp");
    if (!output.good()) {
        cerr << "Failed to write config.json" << endl;
        return;
    }
    output << settings.dump(2) << '\n';
    if (!output.good()) {
        cerr << "Failed to write config.json" << endl;
        return;
    }
    output.close();
    if (rename("config.json.tmp", CONFIG_PATH) != 0) {
        cerr << "Failed to replace config.json" << endl;
    }
}

// Read a travel limit, new name first then the previous config.json key
static bool read_travel_limit(const json& settings, const char* name, const char* previous_name, int& value) {
    if (settings.contains(name)) {
        value = settings[name].get<int>();
        return true;
    }
    if (settings.contains(previous_name)) {
        value = settings[previous_name].get<int>();
        return true;
    }
    return false;
}
