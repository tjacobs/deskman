#pragma once

#include <opencv2/opencv.hpp>

using namespace std;

class Camera {
public:
    Camera();
    ~Camera();
    
    bool initialize();
    bool captureFrame(cv::Mat& frame);
    void release();

    // Camera parameters
    const int width = 640;
    const int height = 480;
    const int framerate = 10;

private:
    cv::VideoCapture cap;
    bool isRaspberryPi;
    int cameraIndex;
    bool capturing;
};
