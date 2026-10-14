/*
 * Settings loaded from config.json
*/

#pragma once

// System
#include <string>

// Answer an incoming call after this many seconds, 0 waits for a tap on Accept
const int AUTO_ANSWER_SECONDS = 5;

// Log in as device 0 when there is no config and the hostname has no trailing number, so a test machine never takes a real robot's name
const int FALLBACK_DEVICE_ID = 0;

// Random bytes in the key that proves this robot owns its name, sent as hex
const int ROBOT_KEY_BYTES = 32;

// Settings file contents
struct TeleportConfig {
    int autoAnswer = AUTO_ANSWER_SECONDS;
    int deviceId;
    std::string key;
    std::string phone;
    bool loaded = true;
};

// Keep only the digits of a phone number, so 415 629 2692 5 and 41562926925 are the same
std::string normalizePhone(const std::string& text);

// Read and write config.json in the project directory, one level above the binary
TeleportConfig loadConfig();
void saveConfig(const TeleportConfig& config);
