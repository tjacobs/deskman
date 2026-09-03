/*
 * Settings loaded from config.json
*/

#pragma once

// Answer an incoming call after this many seconds, 0 waits for a tap on Accept
const int AUTO_ANSWER_SECONDS = 5;

// Log in as teleport1 when the hostname has no trailing number
const int FALLBACK_DEVICE_ID = 1;

// Settings file contents
struct TeleportConfig {
    int autoAnswer = AUTO_ANSWER_SECONDS;
    int deviceId;
    bool statusOpen = false;
};

// Read and write ./config.json
TeleportConfig loadConfig();
void saveConfig(const TeleportConfig& config);
void saveStatusOpen(bool open);
