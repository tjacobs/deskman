#pragma once

// OpenCV
#include <opencv2/opencv.hpp>

// System
#include <string>

// Namespace
using namespace std;

// Keep the first stderr, the camera silences the real one and ffmpeg errors would be lost
void keep_recorder_errors();

// Record the camera and the microphone to an mp4, and say how it is going
bool start_recording(const string& directory, int cameraIndex);
void stop_recording();
void reap_recording();
bool recording();
double recording_seconds();

// Take the newest frame ffmpeg sent back, so the face can still be previewed and tracked
bool take_recording_frame(cv::Mat& frame);
