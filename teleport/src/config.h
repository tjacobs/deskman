/*
 * Settings loaded from config.json
*/

#pragma once

// System
#include <string>

// Answer an incoming call after this many seconds, 0 waits for a tap on Accept
const int AUTO_ANSWER_SECONDS = 5;

// Log in as device 1 when the hostname has no trailing number
const int FALLBACK_DEVICE_ID = 1;

// Random bytes in the key that proves this robot owns its name, sent as hex
const int ROBOT_KEY_BYTES = 32;

// Settings file contents
struct TeleportConfig {
    int autoAnswer = AUTO_ANSWER_SECONDS;
    int deviceId;
    std::string key;
    bool loaded = true;
};

// Read and write config.json in the project directory, one level above the binary
TeleportConfig loadConfig();
void saveConfig(const TeleportConfig& config);
