#pragma once

// System
#include <string>

// Namespace
using namespace std;

// Recheck the INA219 about once a second
void check_battery();

// Empty when the meter is missing
string battery_text();

// Filtered pack voltage, or 0 when unread
float battery_voltage();

// 0 to 100 from the LiPo curve, or 0 when unread
int battery_percent();
