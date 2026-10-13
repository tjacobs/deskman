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
#include <sys/stat.h>
#include <unistd.h>

// Namespace
using namespace std;
using json = nlohmann::json;

// Fallback when the binary path cannot be read
static const char* CONFIG_PATH = "config.json";

// The device number used to live here, it is folded into config.json on first run
static const char* DEVICE_ID_PATH = "device.id";

// Source of the random key bytes
static const char* RANDOM_PATH = "/dev/urandom";

// Only this user may read the file, it holds the key
static const mode_t CONFIG_MODE = 0600;

static void migrateDeviceIdFile(TeleportConfig& config);
static int hostnameDeviceId();
static void ensureKey(TeleportConfig& config);
static string makeKey();

// Config sits in the project directory, one level above the binary in build
static string configFilePath() {
    char executablePath[4096];
    ssize_t length = readlink("/proc/self/exe", executablePath, sizeof(executablePath) - 1);
    if (length <= 0) return CONFIG_PATH;
    executablePath[length] = '\0';

    // Drop the binary name, then step up out of build
    string path = executablePath;
    size_t slash = path.find_last_of('/');
    if (slash == string::npos) return CONFIG_PATH;
    return path.substr(0, slash) + "/../config.json";
}

// Load config.json from the project directory, write the defaults out when it is missing
TeleportConfig loadConfig() {
    TeleportConfig config;
    config.deviceId = hostnameDeviceId();
    string path = configFilePath();

    // Create the file the first time so the settings are easy to find
    ifstream in(path);
    if (!in.good()) {
        config.key = makeKey();
        migrateDeviceIdFile(config);
        saveConfig(config);
        cout << "Created " << path << " from defaults" << endl;
        return config;
    }

    // Keep the default for anything the file leaves out
    try {
        json settings;
        in >> settings;
        if (settings.contains("autoAnswer")) config.autoAnswer = settings["autoAnswer"].get<int>();
        if (settings.contains("deviceId")) config.deviceId = settings["deviceId"].get<int>();
        if (settings.contains("key")) config.key = settings["key"].get<string>();
    } catch (const exception& error) {
        cerr << "Failed to parse config.json: " << error.what() << endl;
        config.loaded = false;
    }

    // An old device.id still wins, so a machine keeps its name across the upgrade
    in.close();
    migrateDeviceIdFile(config);

    // Give a robot from before keys one now
    ensureKey(config);

    // Treat a negative wait as waiting for Accept
    if (config.autoAnswer < 0) config.autoAnswer = 0;
    if (config.deviceId < 1) config.deviceId = hostnameDeviceId();
    return config;
}

// Make and save a key when the file has none, a robot keeps its key for good after that
static void ensureKey(TeleportConfig& config) {
    if (config.key.size() == static_cast<size_t>(ROBOT_KEY_BYTES * 2)) return;

    // Leave a file that failed to parse alone, saving would wipe its other settings
    if (!config.loaded) return;
    config.key = makeKey();
    saveConfig(config);
    cout << "Made this robot's login key" << endl;
}

// Read random bytes and write them out as hex
static string makeKey() {
    ifstream random(RANDOM_PATH, ios::binary);
    unsigned char bytes[ROBOT_KEY_BYTES];
    random.read(reinterpret_cast<char*>(bytes), sizeof(bytes));
    if (random.gcount() != ROBOT_KEY_BYTES) {
        cerr << "Could not read " << RANDOM_PATH << endl;
        return "";
    }

    // Two hex digits per byte
    static const char* HEX_DIGITS = "0123456789abcdef";
    string key;
    for (unsigned char byte : bytes) {
        key += HEX_DIGITS[byte >> 4];
        key += HEX_DIGITS[byte & 0x0f];
    }
    return key;
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

// Write config.json in the project directory
void saveConfig(const TeleportConfig& config) {
    try {
        json settings = {{"autoAnswer", config.autoAnswer}, {"deviceId", config.deviceId}, {"key", config.key}};
        string path = configFilePath();
        string temporaryPath = path + ".tmp";
        ofstream out(temporaryPath);
        if (!out.good()) {
            cerr << "Failed to write config.json" << endl;
            return;
        }
        chmod(temporaryPath.c_str(), CONFIG_MODE);
        out << settings.dump(2) << '\n';
        if (!out.good()) {
            cerr << "Failed to write config.json" << endl;
            return;
        }
        out.close();
        if (rename(temporaryPath.c_str(), path.c_str()) != 0) {
            cerr << "Failed to replace config.json" << endl;
        }
    } catch (const exception& error) {
        cerr << "Failed to write config.json: " << error.what() << endl;
    }
}
