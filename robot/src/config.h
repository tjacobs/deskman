#pragma once

// System
#include <string>

// Board names found in the device tree model, and handed back by board_name
inline const char* BOARD_PI_NAME = "Pi";
inline const char* BOARD_ORIN_NAME = "Orin";

// Settings read from config.json, the defaults run the robot with no file
struct AppConfig {
    bool useCamera = true;
    bool faceTracking = true;
    int pan_min = 100;
    int pan_max = 2200;
    int tilt_min = 440;
    int tilt_max = 840;
    int hat_min = 2040;
    int hat_max = 3400;
    int hat_dir = 1;
    bool has_servo_limits = true;
    bool loaded = true;
};

// Read config.json, writing it from these defaults when the file is missing
AppConfig loadConfig();

// Write config.json, keeping the servo travel limits already in the file
void saveConfig(const AppConfig& config);

// Read the board name from the device tree, empty when it is neither one we know
std::string board_name();
