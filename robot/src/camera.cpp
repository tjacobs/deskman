#include "camera.hpp"
#include <iostream>
#include <filesystem>
#include <fstream>
#include <thread>
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <opencv2/core/utils/logger.hpp>

using namespace std;

static const int MAX_VIDEO_INDEX = 8;
static const int STEREO_VIDEO_COUNT = 4;
static const int PREFERRED_STEREO_INDEX = 2;
static const int CAPTURE_RETRIES = 3;
static const int CAPTURE_RETRY_WAIT_MS = 100;
static const int PIPELINE_RETRY_WAIT_SECONDS = 1;

static bool video_device_present();
static int count_video_devices();
static bool open_usb_camera(cv::VideoCapture& cap, int& camera_index, int width, int height, int framerate);

// Detect Raspberry Pi for the libcamera path
Camera::Camera() {
    // Check if we're running on a Raspberry Pi
    isRaspberryPi = false;
    if (filesystem::exists("/proc/device-tree/model")) {
        ifstream model("/proc/device-tree/model");
        string model_str;
        getline(model, model_str);
        isRaspberryPi = model_str.find("Raspberry Pi") != string::npos;
    }

    // Redirect GStreamer logs to /dev/null
    if (isRaspberryPi) {
        freopen("/dev/null", "w", stderr);
    }

    // Start with no selected camera index
    cameraIndex = 0;
    capturing = false;
}

// Open the camera for the current platform
bool Camera::initialize() {
    capturing = false;

    // Close any previous capture handle
    if (cap.isOpened()) {
        cap.release();
    }

    // Skip OpenCV open when no camera device is attached
    if (!video_device_present() && !isRaspberryPi) {
        cerr << "Error: No camera device found, continuing without camera" << endl;
        return false;
    }

    // Use libcamera on Raspberry Pi
    if (isRaspberryPi) {
        string pipeline = "libcamerasrc ! "
                         "video/x-raw,width=" + to_string(width) +
                         ",height=" + to_string(height) +
                         ",framerate=" + to_string(framerate) + "/1,format=BGR ! "
                         "videoconvert ! "
                         "appsink drop=true sync=false";

        // Retry the GStreamer pipeline a few times
        for (int attempt = 0; attempt < CAPTURE_RETRIES; attempt++) {
            cap.open(pipeline, cv::CAP_GSTREAMER);
            if (cap.isOpened()) {
                cap.set(cv::CAP_PROP_BUFFERSIZE, 1);
                capturing = true;
                return true;
            }
            cerr << "Failed to open camera with GStreamer pipeline (attempt " << (attempt + 1) << "/" << CAPTURE_RETRIES << ")" << endl;
            this_thread::sleep_for(chrono::seconds(PIPELINE_RETRY_WAIT_SECONDS));
        }
        return false;
    }

    // Open a USB camera through V4L2
    capturing = open_usb_camera(cap, cameraIndex, width, height, framerate);
    return capturing;
}

// Read one frame, reopening the camera if needed
bool Camera::captureFrame(cv::Mat& frame) {
    // Skip after an intentional close so stop does not reopen the camera
    if (!capturing) return false;

    // Reinitialize when the capture handle was lost
    if (!cap.isOpened()) {
        cerr << "Camera is not opened, attempting to reinitialize..." << endl;
        if (!initialize()) {
            return false;
        }
    }

    // Retry a few times before giving up
    for (int attempt = 0; attempt < CAPTURE_RETRIES; attempt++) {
        if (!capturing) return false;
        bool success = cap.read(frame);
        if (success && !frame.empty()) {
            return true;
        }
        cerr << "Failed to capture frame (attempt " << (attempt + 1) << "/" << CAPTURE_RETRIES << ")" << endl;

        // Reopen libcamera after a failed read on Raspberry Pi
        if (isRaspberryPi) {
            cap.release();
            this_thread::sleep_for(chrono::milliseconds(CAPTURE_RETRY_WAIT_MS));
            if (!capturing || !initialize()) return false;
        } else {
            this_thread::sleep_for(chrono::milliseconds(CAPTURE_RETRY_WAIT_MS));
        }
    }
    return false;
}

// Release the capture device
void Camera::release() {
    capturing = false;
    if (cap.isOpened()) {
        cap.release();
        cout << "Camera hardware released" << endl;
    }
}

Camera::~Camera() {
    if (cap.isOpened()) {
        cap.release();
    }
}

// True if any /dev/videoN device node exists
static bool video_device_present() {
    return count_video_devices() > 0;
}

// Count attached /dev/videoN nodes
static int count_video_devices() {
    int video_count = 0;
    for (int index = 0; index < MAX_VIDEO_INDEX; index++) {
        if (filesystem::exists("/dev/video" + to_string(index))) video_count++;
    }
    return video_count;
}

// Try each video node until one returns a frame
static bool open_usb_camera(cv::VideoCapture& cap, int& camera_index, int width, int height, int framerate) {
    cout << "Starting camera..." << endl;

    // Prefer camera 2 when a stereo pair exposes four nodes
    int probe_order[MAX_VIDEO_INDEX];
    int probe_count = 0;
    int video_count = count_video_devices();
    if (video_count == STEREO_VIDEO_COUNT) {
        probe_order[probe_count++] = PREFERRED_STEREO_INDEX;
        for (int index = 0; index < MAX_VIDEO_INDEX; index++) {
            if (index == PREFERRED_STEREO_INDEX) continue;
            probe_order[probe_count++] = index;
        }
    } else {
        for (int index = 0; index < MAX_VIDEO_INDEX; index++) probe_order[probe_count++] = index;
    }

    // Hide V4L2 probe warnings while trying nodes
    auto log_level = cv::utils::logging::getLogLevel();
    cv::utils::logging::setLogLevel(cv::utils::logging::LOG_LEVEL_ERROR);

    // Probe each candidate until a real capture node works
    for (int order_index = 0; order_index < probe_count; order_index++) {
        int index = probe_order[order_index];

        // Skip indexes with no device node
        if (!filesystem::exists("/dev/video" + to_string(index))) continue;

        // Close any prior handle, then try this video index
        if (cap.isOpened()) cap.release();
        if (!cap.open(index, cv::CAP_V4L2)) continue;

        // Apply the requested capture size and rate
        cap.set(cv::CAP_PROP_FRAME_WIDTH, width);
        cap.set(cv::CAP_PROP_FRAME_HEIGHT, height);
        cap.set(cv::CAP_PROP_FPS, framerate);
        cap.set(cv::CAP_PROP_BUFFERSIZE, 1);

        // Skip metadata-only nodes that open but never return frames
        cv::Mat test_frame;
        if (cap.read(test_frame) && !test_frame.empty()) {
            camera_index = index;
            cv::utils::logging::setLogLevel(log_level);
            cout << "Using camera " << index << endl;
            return true;
        }

        // Release and try the next index
        cap.release();
    }

    // Restore logging after a failed probe
    cv::utils::logging::setLogLevel(log_level);
    cerr << "Failed to open camera, continuing without camera" << endl;
    return false;
}
