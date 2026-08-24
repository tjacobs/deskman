#include "config.h"
#include "json.hpp"
#include <fstream>
#include <iostream>

using namespace std;
using json = nlohmann::json;

static const char* CONFIG_PATH = "config.json";

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

        // Use servo limits only when every axis min and max is present
        bool has_min_x = j.contains("min_x");
        bool has_max_x = j.contains("max_x");
        bool has_min_y = j.contains("min_y");
        bool has_max_y = j.contains("max_y");
        bool has_min_hat = j.contains("min_hat");
        bool has_max_hat = j.contains("max_hat");
        config.has_servo_limits = has_min_x && has_max_x && has_min_y && has_max_y && has_min_hat && has_max_hat;
        if (has_min_x) config.min_x = j["min_x"].get<int>();
        if (has_max_x) config.max_x = j["max_x"].get<int>();
        if (has_min_y) config.min_y = j["min_y"].get<int>();
        if (has_max_y) config.max_y = j["max_y"].get<int>();
        if (has_min_hat) config.min_hat = j["min_hat"].get<int>();
        if (has_max_hat) config.max_hat = j["max_hat"].get<int>();
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
    }
    return config;
}

// Write ./config.json, omit servo limits unless they were already configured
void saveConfig(const AppConfig& config) {
    json j = {
        {"useCamera", config.useCamera},
        {"faceTracking", config.faceTracking}
    };

    // Keep existing travel limits, do not invent defaults
    if (config.has_servo_limits) {
        j["min_x"] = config.min_x;
        j["max_x"] = config.max_x;
        j["min_y"] = config.min_y;
        j["max_y"] = config.max_y;
        j["min_hat"] = config.min_hat;
        j["max_hat"] = config.max_hat;
    }
    try {
        ofstream out(CONFIG_PATH);
        out << j.dump(2);
    } catch (const exception& error) {
        cerr << "Failed to write config.json: " << error.what() << endl;
    }
}
