#pragma once

// System
#include <string>

// Namespace
using namespace std;

// Recheck the fan and CPU temperature about once a second
void check_fan();

// Return the fan warning, empty when the fan is keeping up
string fan_warning_text();

// Return the heat warning, empty when the CPU is at or below the warn point
string temperature_warning_text();
