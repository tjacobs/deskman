// Local
#include "camera.h"

// OpenCV
#include <opencv2/core/utils/logger.hpp>

// System
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

// Linux V4L2
#ifdef __linux__
#include <fcntl.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <unistd.h>
#endif

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

#ifdef __linux__

// One control and the value to give it
struct CameraControl {
    unsigned int id;
    int value;
};

// Settings for one camera model, matched on the name the driver reports
struct CameraModel {
    const char* nameMatch;
    vector<CameraControl> controls;
};

// Picture settings for each known camera model
static const vector<CameraModel> CAMERA_MODELS = {
    {"Arducam 1080P Low Light", {
        {V4L2_CID_BACKLIGHT_COMPENSATION, 0},
        {V4L2_CID_CONTRAST, 64},
        {V4L2_CID_GAMMA, 72},
        {V4L2_CID_SATURATION, 90},
    }},
};

#endif

// Later in this file
static bool video_device_present();
static bool libcamera_camera_present();
static int count_video_devices();
static bool open_USB_camera(cv::VideoCapture& capture, int& camera_index, int width, int height, int framerate);
#ifdef __linux__
static bool set_control(int fd, unsigned int id, int value);
#endif

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
            set_camera_settings(index);
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

// Set the picture settings listed for this camera model
void set_camera_settings(int camera_index) {
#ifdef __linux__
    // Open the node alongside whoever is capturing from it
    string device = "/dev/video" + to_string(camera_index);
    int fd = open(device.c_str(), O_RDWR);
    if (fd < 0)
        return;

    // Read the name the driver gives this camera
    v4l2_capability capability = {};
    string name;
    if (ioctl(fd, VIDIOC_QUERYCAP, &capability) == 0)
        name = reinterpret_cast<const char*>(capability.card);

    // Set each control listed for a matching model, the settings stay on the camera for teleport and recordings too
    for (const CameraModel& cameraModel : CAMERA_MODELS) {
        if (name.find(cameraModel.nameMatch) == string::npos)
            continue;
        for (const CameraControl& control : cameraModel.controls)
            set_control(fd, control.id, control.value);
        cout << "Camera: " << cameraModel.nameMatch << endl;
    }
    close(fd);
#endif
}

// True when the camera lists MJPEG at this size and rate
bool camera_offers_MJPEG(int camera_index, int width, int height, int framerate) {
#ifdef __linux__
    // Open the node to read its format list
    string device = "/dev/video" + to_string(camera_index);
    int fd = open(device.c_str(), O_RDWR);
    if (fd < 0)
        return false;

    // Walk the rates listed for this size, a size it lacks lists none
    bool offered = false;
    v4l2_frmivalenum interval = {};
    interval.pixel_format = V4L2_PIX_FMT_MJPEG;
    interval.width = width;
    interval.height = height;
    for (interval.index = 0; ioctl(fd, VIDIOC_ENUM_FRAMEINTERVALS, &interval) == 0; interval.index++) {
        if (interval.type == V4L2_FRMIVAL_TYPE_DISCRETE && interval.discrete.numerator * framerate == interval.discrete.denominator)
            offered = true;
    }
    close(fd);
    return offered;
#else
    return false;
#endif
}

#ifdef __linux__

// Set one control when the camera has it, kept inside the range it allows
static bool set_control(int fd, unsigned int id, int value) {
    // Skip controls this camera does not have
    v4l2_queryctrl query = {};
    query.id = id;
    if (ioctl(fd, VIDIOC_QUERYCTRL, &query) != 0 || (query.flags & V4L2_CTRL_FLAG_DISABLED))
        return false;

    // Clamp to the camera's own range, then set it
    v4l2_control control = {};
    control.id = id;
    control.value = max(query.minimum, min(query.maximum, value));
    return ioctl(fd, VIDIOC_S_CTRL, &control) == 0;
}

#endif
