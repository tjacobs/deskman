// Local
#include "camera.h"

// OpenCV
#include <opencv2/core/utils/logger.hpp>

// System
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>

// Namespace
using namespace std;

// How many /dev/videoN nodes to probe, and which one a stereo pair captures on
static const int MAX_VIDEO_INDEX = 8;
static const int STEREO_VIDEO_COUNT = 4;
static const int PREFERRED_STEREO_INDEX = 2;

// Retry a failed open or read a few times
static const int CAPTURE_RETRIES = 3;
static const int CAPTURE_RETRY_WAIT_MS = 100;
static const int PIPELINE_RETRY_WAIT_SECONDS = 1;

// Ask rpicam whether a CSI camera is attached
static const char *LIBCAMERA_LIST_COMMAND = "rpicam-hello --list-cameras 2>&1";

// Later in this file
static bool video_device_present();
static bool libcamera_camera_present();
static int count_video_devices();
static bool open_USB_camera(cv::VideoCapture& capture, int& camera_index, int width, int height, int framerate);

// Detect Raspberry Pi for the libcamera path
Camera::Camera() {
    // Read the device tree model name
    isRaspberryPi = false;
    if (filesystem::exists("/proc/device-tree/model")) {
        ifstream model("/proc/device-tree/model");
        string model_name;
        getline(model, model_name);
        isRaspberryPi = model_name.find("Raspberry Pi") != string::npos;
    }

    // Redirect GStreamer logs to /dev/null
    if (isRaspberryPi && freopen("/dev/null", "w", stderr) == nullptr) {
        cerr << "Failed to silence GStreamer logs" << endl;
    }

    // Start with no selected camera index
    cameraIndex = 0;
    capturing = false;
}

// Open the camera for the current platform
bool Camera::initialize() {
    // Stay stopped until a device opens
    capturing = false;

    // Close any previous capture handle
    if (capture.isOpened())
        capture.release();

    // Say this before any probe so a missing camera fails in the startup log
    cout << "Starting camera..." << endl;
    fflush(stdout);

    // Skip OpenCV open when nothing is plugged in
    bool have_usb = video_device_present();
    bool have_libcamera = isRaspberryPi && libcamera_camera_present();
    if (!have_usb && !have_libcamera) {
        cout << "No camera plugged in, face tracking off." << endl;
        fflush(stdout);
        return false;
    }

    // Use libcamera when a CSI camera answered
    if (have_libcamera) {
        string pipeline = "libcamerasrc ! "
                         "video/x-raw,width=" + to_string(width) +
                         ",height=" + to_string(height) +
                         ",framerate=" + to_string(framerate) + "/1,format=BGR ! "
                         "videoconvert ! "
                         "appsink drop=true sync=false";

        // Retry the GStreamer pipeline a few times
        for (int attempt = 0; attempt < CAPTURE_RETRIES; attempt++) {
            capture.open(pipeline, cv::CAP_GSTREAMER);
            if (capture.isOpened()) {
                capture.set(cv::CAP_PROP_BUFFERSIZE, 1);
                capturing = true;
                return true;
            }
            cerr << "Failed to open camera with GStreamer pipeline (attempt " << (attempt + 1) << "/" << CAPTURE_RETRIES << ")" << endl;
            this_thread::sleep_for(chrono::seconds(PIPELINE_RETRY_WAIT_SECONDS));
        }
    }

    // Open a USB camera through V4L2
    if (have_usb) {
        capturing = open_USB_camera(capture, cameraIndex, width, height, framerate);
        return capturing;
    }

    cout << "No camera plugged in, face tracking off." << endl;
    fflush(stdout);
    return false;
}

// Read one frame, reopening the camera if needed
bool Camera::captureFrame(cv::Mat& frame) {
    // Skip after an intentional close so stop does not reopen the camera
    if (!capturing)
        return false;

    // Reinitialize when the capture handle was lost
    if (!capture.isOpened()) {
        cerr << "Camera is not opened, attempting to reinitialize..." << endl;
        if (!initialize()) {
            return false;
        }
    }

    // Retry a few times before giving up
    for (int attempt = 0; attempt < CAPTURE_RETRIES; attempt++) {
        if (!capturing)
            return false;
        bool success = capture.read(frame);
        if (success && !frame.empty()) {
            return true;
        }
        cerr << "Failed to capture frame (attempt " << (attempt + 1) << "/" << CAPTURE_RETRIES << ")" << endl;

        // Reopen libcamera after a failed read on Raspberry Pi
        if (isRaspberryPi) {
            capture.release();
            this_thread::sleep_for(chrono::milliseconds(CAPTURE_RETRY_WAIT_MS));
            if (!capturing || !initialize())
                return false;
        } else {
            this_thread::sleep_for(chrono::milliseconds(CAPTURE_RETRY_WAIT_MS));
        }
    }

    // Every retry came back empty
    return false;
}

// Release the capture device
void Camera::release() {
    capturing = false;
    if (capture.isOpened()) {
        capture.release();
    }
}

// Drop the device on the way out
Camera::~Camera() {
    if (capture.isOpened()) {
        capture.release();
    }
}

// True if any /dev/videoN device node exists
static bool video_device_present() {
    return count_video_devices() > 0;
}

// True when rpicam lists a CSI camera, ISP leftover nodes do not count
static bool libcamera_camera_present() {
    if (!filesystem::exists("/usr/bin/rpicam-hello"))
        return false;

    FILE *pipe = popen(LIBCAMERA_LIST_COMMAND, "r");
    if (pipe == nullptr)
        return false;

    string output;
    char buffer[256];
    while (fgets(buffer, sizeof(buffer), pipe) != nullptr)
        output += buffer;
    pclose(pipe);
    return !output.empty() && output.find("No cameras available") == string::npos;
}

// Count attached /dev/videoN nodes
static int count_video_devices() {
    // Walk the whole range so a gap does not stop the count
    int video_count = 0;
    for (int index = 0; index < MAX_VIDEO_INDEX; index++) {
        if (filesystem::exists("/dev/video" + to_string(index)))
            video_count++;
    }
    return video_count;
}

// Try each video node until one returns a frame
static bool open_USB_camera(cv::VideoCapture& capture, int& camera_index, int width, int height, int framerate) {
    // Try the stereo capture node first when a stereo pair is attached
    int probe_order[MAX_VIDEO_INDEX];
    int probe_count = 0;
    int video_count = count_video_devices();
    if (video_count == STEREO_VIDEO_COUNT) {
        probe_order[probe_count++] = PREFERRED_STEREO_INDEX;
        for (int index = 0; index < MAX_VIDEO_INDEX; index++) {
            if (index == PREFERRED_STEREO_INDEX)
                continue;
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
        if (!filesystem::exists("/dev/video" + to_string(index)))
            continue;

        // Close any prior handle, then try this video index
        if (capture.isOpened())
            capture.release();
        if (!capture.open(index, cv::CAP_V4L2))
            continue;

        // Apply the requested capture size and rate
        capture.set(cv::CAP_PROP_FRAME_WIDTH, width);
        capture.set(cv::CAP_PROP_FRAME_HEIGHT, height);
        capture.set(cv::CAP_PROP_FPS, framerate);
        capture.set(cv::CAP_PROP_BUFFERSIZE, 1);

        // Skip metadata-only nodes that open but never return frames
        cv::Mat test_frame;
        if (capture.read(test_frame) && !test_frame.empty()) {
            camera_index = index;
            cv::utils::logging::setLogLevel(log_level);
            return true;
        }

        // Release and try the next index
        capture.release();
    }

    // Restore logging after a failed probe
    cv::utils::logging::setLogLevel(log_level);
    cerr << "Failed to open camera, continuing without camera" << endl;
    return false;
}
