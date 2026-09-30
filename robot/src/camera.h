#pragma once

// OpenCV
#include <opencv2/opencv.hpp>

// System
#include <string>

// Namespace
using namespace std;

// Set the picture settings listed for this camera model
void set_camera_settings(int camera_index);

// True when the camera lists MJPEG at this size and rate
bool camera_offers_MJPEG(int camera_index, int width, int height, int framerate);

// Open a USB or Raspberry Pi camera and hand out frames
class Camera {
public:
    // Set up and tear down the capture device
    Camera();
    ~Camera();

    // Open the camera, read a frame, and close it again
    bool initialize();
    bool captureFrame(cv::Mat& frame);
    void release();

    // Which /dev/videoN is open, so another program can be pointed at it
    int index() const { return cameraIndex; }

    // Capture size and rate
    const int width = 640;
    const int height = 480;
    const int framerate = 30;

private:
    // Open handle, detected platform, and running state
    cv::VideoCapture capture;
    bool isRaspberryPi;
    int cameraIndex;
    bool capturing;
};
