/*
 * Settings loaded from config.json
*/

// Local
#include "config.h"

// JSON
#include "json.hpp"

// System
#include <fstream>
#include <iostream>

// Namespace
using namespace std;
using json = nlohmann::json;

// Settings sit next to the binary, in the working directory
static const char* CONFIG_PATH = "config.json";

// Load ./config.json, write the defaults out when it is missing
TeleportConfig loadConfig() {
    TeleportConfig config;

    // Create the file the first time so the settings are easy to find
    ifstream in(CONFIG_PATH);
    if (!in.good()) {
        saveConfig(config);
        cout << "Created " << CONFIG_PATH << " from defaults" << endl;
        return config;
    }

    // Keep the default for anything the file leaves out
    try {
        json settings;
        in >> settings;
        if (settings.contains("autoAnswer")) config.autoAnswer = settings["autoAnswer"].get<int>();
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
    }

    // Treat a negative wait as waiting for Accept
    if (config.autoAnswer < 0) config.autoAnswer = 0;
    return config;
}

// Write ./config.json
void saveConfig(const TeleportConfig& config) {
    try {
        json settings = {{"autoAnswer", config.autoAnswer}};
        ofstream out(CONFIG_PATH);
        out << settings.dump(2);
    } catch (const exception& error) {
        cerr << "Failed to write config.json: " << error.what() << endl;
    }
}
