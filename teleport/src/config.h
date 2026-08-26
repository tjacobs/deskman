/*
 * Settings loaded from config.json
*/

#pragma once

// Answer an incoming call after this many seconds, 0 waits for a tap on Accept
const int AUTO_ANSWER_SECONDS = 5;

// Settings file contents
struct TeleportConfig {
    int autoAnswer = AUTO_ANSWER_SECONDS;
};

// Read and write ./config.json
TeleportConfig loadConfig();
void saveConfig(const TeleportConfig& config);
