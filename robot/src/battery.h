#pragma once

// System
#include <string>

// Namespace
using namespace std;

// Recheck the INA219 battery voltage and current
void check_battery();

// Return the face label, empty when the meter is missing
string battery_text();

// Return the filtered pack voltage, 0 when unread
float battery_voltage();

// Return charge from 0 to 100 on the pack curve, 0 when unread
int battery_percent();

// Return the filtered shunt current in amps, sign follows the meter wiring
float battery_current();
