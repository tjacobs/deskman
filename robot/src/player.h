#pragma once

// OpenCV
#include <opencv2/opencv.hpp>

// System
#include <filesystem>
#include <string>
#include <vector>

// Namespace
using namespace std;

// One recording on disk, as the list shows it
struct Recording {
    string path;
    string dateText;
    double seconds;
    long long bytes;
    filesystem::file_time_type written;
};

// List what has been recorded, newest first, lengths follow once measured
vector<Recording> list_recordings(const string& directory);
bool recording_lengths_updated();

// Remove one recording from disk
bool delete_recording(const string& path);

// Play one recording in the robot window, with its sound
bool start_playback(const string& path);
void stop_playback();
void close_playback();
bool playing();

// Take the newest frame ffmpeg sent back, so the window can show it
bool take_playback_frame(cv::Mat& frame);

// Draw the frame the video is up to, across the width of the screen
void draw_playback_frame();

// Name a recording by its file alone, the folder is noise on the screen
string recording_name(const string& path);

// Say a length as minutes and seconds, and a size in megabytes
string length_text(double seconds);
string size_text(long long bytes);
