/*
 * Settings loaded from config.json
*/

// Local
#include "config.h"

// JSON
#include "json.hpp"

// System
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <unistd.h>

// Namespace
using namespace std;
using json = nlohmann::json;

// Settings sit next to the binary, in the working directory
static const char* CONFIG_PATH = "config.json";

// The device number used to live here, it is folded into config.json on first run
static const char* DEVICE_ID_PATH = "device.id";

static void migrateDeviceIdFile(TeleportConfig& config);
static int hostnameDeviceId();

// Load ./config.json, write the defaults out when it is missing
TeleportConfig loadConfig() {
    TeleportConfig config;
    config.deviceId = hostnameDeviceId();

    // Create the file the first time so the settings are easy to find
    ifstream in(CONFIG_PATH);
    if (!in.good()) {
        migrateDeviceIdFile(config);
        saveConfig(config);
        cout << "Created " << CONFIG_PATH << " from defaults" << endl;
        return config;
    }

    // Keep the default for anything the file leaves out
    try {
        json settings;
        in >> settings;
        if (settings.contains("autoAnswer")) config.autoAnswer = settings["autoAnswer"].get<int>();
        if (settings.contains("deviceId")) config.deviceId = settings["deviceId"].get<int>();
        if (settings.contains("statusOpen")) config.statusOpen = settings["statusOpen"].get<bool>();
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
    }

    // An old device.id still wins, so a machine keeps its name across the upgrade
    in.close();
    migrateDeviceIdFile(config);

    // Treat a negative wait as waiting for Accept
    if (config.autoAnswer < 0) config.autoAnswer = 0;
    if (config.deviceId < 1) config.deviceId = hostnameDeviceId();
    return config;
}

// Take the number off the end of the hostname, deskman4 becomes 4
static int hostnameDeviceId() {
    char hostName[256];
    if (gethostname(hostName, sizeof(hostName)) != 0) return FALLBACK_DEVICE_ID;
    hostName[sizeof(hostName) - 1] = '\0';

    // Drop the domain so deskman4.local still reads as 4
    string name = hostName;
    size_t dot = name.find('.');
    if (dot != string::npos) name = name.substr(0, dot);

    // Read the digits at the end of the first label
    size_t end = name.size();
    size_t start = end;
    while (start > 0 && isdigit(static_cast<unsigned char>(name[start - 1]))) start--;
    if (start == end) return FALLBACK_DEVICE_ID;
    int deviceId = atoi(name.c_str() + start);
    if (deviceId < 1) return FALLBACK_DEVICE_ID;
    return deviceId;
}

// Take the number out of an old device.id, write it into config.json, and drop the file
static void migrateDeviceIdFile(TeleportConfig& config) {
    // Nothing to do once the old file is gone
    ifstream in(DEVICE_ID_PATH);
    if (!in.is_open()) return;
    int fileDeviceId = 0;
    in >> fileDeviceId;
    in.close();

    // Leave anything unreadable alone rather than renaming the device
    if (fileDeviceId < 1) {
        cerr << "Ignoring " << DEVICE_ID_PATH << ", it holds no device number" << endl;
        return;
    }

    // Carry it over, then take the old file away so there is one place to look
    config.deviceId = fileDeviceId;
    saveConfig(config);
    if (remove(DEVICE_ID_PATH) != 0) cerr << "Could not remove " << DEVICE_ID_PATH << endl;
    else cout << "Moved device " << fileDeviceId << " from " << DEVICE_ID_PATH << " into " << CONFIG_PATH << endl;
}

// Write ./config.json
void saveConfig(const TeleportConfig& config) {
    try {
        json settings = {{"autoAnswer", config.autoAnswer}, {"deviceId", config.deviceId}, {"statusOpen", config.statusOpen}};
        ofstream out(CONFIG_PATH);
        out << settings.dump(2);
    } catch (const exception& error) {
        cerr << "Failed to write config.json: " << error.what() << endl;
    }
}

// Remember whether the peer list was showing
void saveStatusOpen(bool open) {
    TeleportConfig config = loadConfig();
    config.statusOpen = open;
    saveConfig(config);
}
