#pragma once

#include <string>

struct AppConfig {
    bool useCamera = true;
    bool faceTracking = true;
    int min_x = 2230;
    int max_x = 3620;
    int min_y = 2600;
    int max_y = 3000;
    int min_hat = 1470;
    int max_hat = 2800;
    bool has_servo_limits = true;
};

AppConfig loadConfig();
void saveConfig(const AppConfig& config);
