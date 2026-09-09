#pragma once

// OpenCV
#include <opencv2/opencv.hpp>

// SDL
#include <SDL2/SDL.h>

// Local
#include "camera.h"

// System
#include <atomic>
#include <mutex>
#include <thread>
#include <vector>

// Namespace
using namespace std;

// Watch the camera for faces, and draw a preview over the face
class FaceTracker {
public:
    // Load the cascade and open the camera, then close both again
    FaceTracker(bool show_window, bool use_camera);
    ~FaceTracker();

    // Run face detection on a worker thread
    void startTracking();
    void stopTracking();
    bool isTracking() const { return trackingThread.joinable(); }

    // Open and close the camera on its own, so a call can borrow it
    bool initializeCamera();
    void stopCamera();
    bool isCameraAvailable() const { return cameraAvailable; }

    // Where the tracked face sits, and the preview drawn over the face
    bool getFacePosition(float& x, float& y);
    void updateWindow();

private:
    // Worker thread body, and the detection it runs
    void trackingThreadFunction();
    vector<cv::Rect> detectFaces(const cv::Mat& frame);

    // Camera, cascade, and the face the tracker last saw
    Camera camera;
    cv::CascadeClassifier face_cascade;
    cv::Rect currentFace;
    mutex faceMutex;

    // Worker thread, and the flags that start and stop it
    thread trackingThread;
    atomic<bool> shouldQuit{false};
    bool cameraAvailable{false};
    bool showWindow;

    // Latest frame, and the texture the preview is drawn from
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
