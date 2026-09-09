#pragma once

// OpenCV
#include <opencv2/opencv.hpp>

// Namespace
using namespace std;

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

    // Capture size and rate
    const int width = 640;
    const int height = 480;
    const int framerate = 10;

private:
    // Open handle, detected platform, and running state
    cv::VideoCapture capture;
    bool isRaspberryPi;
    int cameraIndex;
    bool capturing;
};
