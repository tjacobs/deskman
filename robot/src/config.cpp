#include "config.h"
#include "json.hpp"
#include <fstream>
#include <iostream>

using namespace std;
using json = nlohmann::ordered_json;

static const char* CONFIG_PATH = "config.json";

// Read a travel limit, new name first then the old config.json key
static bool read_travel_limit(const json& j, const char* name, const char* previous_name, int& value) {
    if (j.contains(name)) {
        value = j[name].get<int>();
        return true;
    }
    if (j.contains(previous_name)) {
        value = j[previous_name].get<int>();
        return true;
    }
    return false;
}

// Load ./config.json, servo motion needs all six travel keys
AppConfig loadConfig() {
    AppConfig config;
    ifstream in(CONFIG_PATH);
    if (!in.good()) {

        // Create config.json from AppConfig defaults, including servo travel
        saveConfig(config);
        cout << "Created " << CONFIG_PATH << " from defaults" << endl;
        return config;
    }
    try {
        json j;
        in >> j;
        if (j.contains("useCamera")) config.useCamera = j["useCamera"].get<bool>();
        if (j.contains("faceTracking")) config.faceTracking = j["faceTracking"].get<bool>();
        if (j.contains("statusOpen")) config.statusOpen = j["statusOpen"].get<bool>();

        // Use servo limits only when every axis min and max is present
        bool has_pan_min = read_travel_limit(j, "pan_min", "min_x", config.pan_min);
        bool has_pan_max = read_travel_limit(j, "pan_max", "max_x", config.pan_max);
        bool has_tilt_min = read_travel_limit(j, "tilt_min", "min_y", config.tilt_min);
        bool has_tilt_max = read_travel_limit(j, "tilt_max", "max_y", config.tilt_max);
        bool has_hat_min = read_travel_limit(j, "hat_min", "min_hat", config.hat_min);
        bool has_hat_max = read_travel_limit(j, "hat_max", "max_hat", config.hat_max);
        config.has_servo_limits = has_pan_min && has_pan_max && has_tilt_min && has_tilt_max && has_hat_min && has_hat_max;
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
    }
    return config;
}

// Write ./config.json, omit servo limits unless they were already configured
void saveConfig(const AppConfig& config) {
    json j;
    j["useCamera"] = config.useCamera;
    j["faceTracking"] = config.faceTracking;
    j["statusOpen"] = config.statusOpen;

    // Keep existing travel limits, do not invent defaults
    if (config.has_servo_limits) {
        j["pan_min"] = config.pan_min;
        j["pan_max"] = config.pan_max;
        j["tilt_min"] = config.tilt_min;
        j["tilt_max"] = config.tilt_max;
        j["hat_min"] = config.hat_min;
        j["hat_max"] = config.hat_max;
    }
    try {
        ofstream out(CONFIG_PATH);
        out << j.dump(2);
    } catch (const exception& error) {
        cerr << "Failed to write config.json: " << error.what() << endl;
    }
}

// Remember whether the status bar was showing
void saveStatusOpen(bool open) {
    AppConfig config = loadConfig();
    config.statusOpen = open;
    saveConfig(config);
}
