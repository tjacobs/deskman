#pragma once

#include <opencv2/opencv.hpp>
#include <string>
#include <vector>
#include <thread>
#include <mutex>
#include <atomic>
#include "camera.hpp"
#include <SDL2/SDL.h>
#include <chrono>

using namespace std;

class FaceTracker {
public:
    FaceTracker(bool show_window, bool use_camera);
    ~FaceTracker();

    void startTracking();
    void stopTracking();
    bool isTracking() const { return trackingThread.joinable(); }
    bool isCameraAvailable() const { return cameraAvailable; }
    bool initializeCamera();
    void stopCamera();

    bool getFacePosition(float& x, float& y);
    void updateWindow();

private:
    void trackingThreadFunction();
    vector<cv::Rect> detectFaces(const cv::Mat& frame);

    Camera camera;
    cv::Rect currentFace;
    cv::CascadeClassifier face_cascade;
    mutex faceMutex;
    thread trackingThread;
    atomic<bool> shouldQuit{false};
    bool cameraAvailable{false};
    bool showWindow;

    mutex frameMutex;
    cv::Mat currentFrame;
    cv::Mat renderFrame;
    cv::Mat previewRgb;
    bool hasNewFrame = false;
    bool hasPreviewFrame = false;
    SDL_Texture* previewTexture = nullptr;
    int previewTextureWidth = 0;
    int previewTextureHeight = 0;
};
