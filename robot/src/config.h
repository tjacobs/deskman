#pragma once

#include <string>

struct AppConfig {
    bool useCamera = true;
    bool faceTracking = true;
    int pan_min = 2230;
    int pan_max = 3620;
    int tilt_min = 2600;
    int tilt_max = 3000;
    int hat_min = 1470;
    int hat_max = 2800;
    bool has_servo_limits = true;
};

AppConfig loadConfig();
void saveConfig(const AppConfig& config);
