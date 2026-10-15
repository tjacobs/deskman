#pragma once

// OpenCV
#include <opencv2/opencv.hpp>

// SDL
#include <SDL2/SDL.h>

// Local
#include "camera.h"

// System
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <vector>

// Namespace
using namespace std;

// Eyes, nose, and mouth corners the detector finds on a face
static const int LANDMARK_COUNT = 5;
typedef array<cv::Point, LANDMARK_COUNT> FaceLandmarks;

// Watch the camera for faces, and draw a preview over the face
class FaceTracker {
public:
    // Load the face detector and open the camera, then close both again
    FaceTracker(bool show_window, bool use_camera);
    ~FaceTracker();

    // Run face detection on a worker thread
    void startTracking();
    void stopTracking();
    bool isTracking() const { return tracking.load(); }

    // Open and close the camera on its own, so a call can borrow it
    bool initializeCamera();
    void stopCamera();
    bool isCameraAvailable() const { return cameraAvailable; }
    int cameraIndex() const { return camera.index(); }

    // Where the tracked face sits, and the preview drawn over the face
    bool getFacePosition(float& x, float& y);
    void updateWindow();
    atomic<bool> showWindow;

    // Top edge of the preview on screen, negative before the first frame
    int previewTop() const;

private:
    // Worker thread bodies, one grabs frames and one looks for faces in them
    void trackingThreadFunction();
    void detectThreadFunction();
    vector<cv::Rect> detectFaces(const cv::Mat& frame, vector<FaceLandmarks>& landmarks);

    // Camera, face detector, and the face the tracker last saw
    Camera camera;
    cv::Ptr<cv::FaceDetectorYN> face_detector;
    cv::Rect currentFace;
    mutex faceMutex;

    // Size of the frames being tracked on, the camera and a recording differ
    int frameWidth = 0;
    int frameHeight = 0;

    // Boxes from the last detection, drawn onto the frames in between
    vector<cv::Rect> lastFaces;
    vector<FaceLandmarks> lastLandmarks;
    size_t largestFace = 0;
    chrono::steady_clock::time_point lastDetectAt{};

    // Frame the detector is working on, handed over by the capture thread
    thread detectThread;
    cv::Mat detectFrame;
    mutex detectMutex;
    condition_variable detectReady;
    bool detectBusy = false;

    // Worker thread, and the flags that start and stop it
    thread trackingThread;
    atomic<bool> tracking{false};
    atomic<bool> shouldQuit{false};
    bool cameraAvailable{false};

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
