#pragma once

// System
#include <string>

// Namespace
using namespace std;

// Record the camera and the microphone to an mp4, and say how it is going
bool start_recording(const string& directory, int cameraIndex);
void stop_recording();
void reap_recording();
bool recording();
double recording_seconds();
