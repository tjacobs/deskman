// Local
#include "tracker.h"
#include "screen.h"
#include "recorder.h"

// System
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>

// Namespace
using namespace std;

// Run the Haar detector well under the camera rate, it is the expensive part
static const int FACE_DETECT_FPS = 3;
static const int FACE_DETECT_GAP_MS = 1000 / FACE_DETECT_FPS;

// How long to wait when the recording has not sent its next frame yet
static const int FRAME_WAIT_MS = 10;

// Pace the preview, reading the camera flat out cooks the Pi for frames nobody sees
static const int PREVIEW_FPS = 30;
static const int PREVIEW_GAP_MS = 1000 / PREVIEW_FPS;

// Preview spans the screen, sitting below the top edge
static const int PREVIEW_TOP = 120;

// Green box on the face being followed, grey on the rest
static const cv::Scalar TRACKED_FACE_COLOR = cv::Scalar(0, 255, 0);
static const cv::Scalar OTHER_FACE_COLOR = cv::Scalar(128, 128, 128);
static const int FACE_BOX_THICKNESS = 2;

// Blur before detection, then accept faces between these sizes
static const int DETECT_BLUR_SIZE = 5;
static const double DETECT_SCALE_FACTOR = 1.1;
static const int DETECT_MIN_NEIGHBORS = 3;
static const int DETECT_MIN_FACE = 20;
static const int DETECT_MAX_FACE = 300;

// Where OpenCV keeps its Haar cascades, Linux first then Mac
static const char* CASCADE_PATHS[] = {
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    "/opt/homebrew/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
};

// Load the face cascade and open the camera
FaceTracker::FaceTracker(bool show_window, bool use_camera): showWindow(show_window) {
    // Load the first cascade file that is present
    bool loaded = false;
    for (const char* path : CASCADE_PATHS) {
        if (filesystem::exists(path) && face_cascade.load(path)) {
            loaded = true;
            break;
        }
    }

    // Fail when the cascade file is missing
    if (!loaded) {
        cerr << "Error: Could not find or load face cascade classifier in any of the following paths:" << endl;
        for (const char* path : CASCADE_PATHS) {
            cerr << "  " << path << endl;
        }
        throw runtime_error("Failed to load face cascade classifier");
    }

    // Open the camera unless tracking is disabled
    if (use_camera) {
        cameraAvailable = camera.initialize();
        if (!cameraAvailable)
            showWindow = false;
    }
}

// Start the detection thread, unless it is already running
void FaceTracker::startTracking() {
    if (tracking.load())
        return;

    // Collect a thread that stopped on its own, so the assign below is safe
    if (trackingThread.joinable())
        trackingThread.join();

    // Only start tracking with a frame source, the camera or a running recording
    if (!cameraAvailable && !recording())
        return;

    // Start tracking thread
    shouldQuit = false;
    tracking = true;
    detectBusy = false;
    trackingThread = thread(&FaceTracker::trackingThreadFunction, this);
    detectThread = thread(&FaceTracker::detectThreadFunction, this);
}

// Follow the largest face in each frame until stopTracking
void FaceTracker::trackingThreadFunction() {
    try {
        while (!shouldQuit) {
            // Take a frame from the recording when one is running, otherwise from the camera
            auto frameStart = chrono::steady_clock::now();
            cv::Mat frame;
            if (recording()) {
                if (!take_recording_frame(frame)) {
                    this_thread::sleep_for(chrono::milliseconds(FRAME_WAIT_MS));
                    continue;
                }
            } else if (!camera.captureFrame(frame)) {
                cerr << "Error: Could not read frame from camera" << endl;
                break;
            }

            // Hand the frame to the detector on a timer, it runs alongside so it never holds up the preview
            auto now = chrono::steady_clock::now();
            if (chrono::duration_cast<chrono::milliseconds>(now - lastDetectAt).count() >= FACE_DETECT_GAP_MS) {
                unique_lock<mutex> detectLock(detectMutex);
                if (!detectBusy) {
                    lastDetectAt = now;
                    frame.copyTo(detectFrame);
                    detectBusy = true;
                    detectLock.unlock();
                    detectReady.notify_one();
                }
            }

            // Draw the boxes from the last detection and update frame buffer if the preview is on
            if (showWindow.load()) {
                unique_lock<mutex> faceLock(faceMutex);
                vector<cv::Rect> faces = lastFaces;
                size_t largest = largestFace;
                faceLock.unlock();
                for (size_t i = 0; i < faces.size(); i++) {
                    cv::Scalar color = i == largest ? TRACKED_FACE_COLOR : OTHER_FACE_COLOR;
                    cv::rectangle(frame, faces[i], color, FACE_BOX_THICKNESS);
                }
                unique_lock<mutex> frameLock(frameMutex);
                frame.copyTo(currentFrame);
                hasNewFrame = true;
                frameLock.unlock();
            }

            // Hold the loop to the preview rate
            auto spent = chrono::duration_cast<chrono::milliseconds>(chrono::steady_clock::now() - frameStart).count();
            if (spent < PREVIEW_GAP_MS)
                this_thread::sleep_for(chrono::milliseconds(PREVIEW_GAP_MS - spent));
        }
    } catch (const exception& error) {
        cerr << "Face tracking error: " << error.what() << endl;
    }

    // Let startTracking run again after the thread stops on its own
    tracking = false;
    detectReady.notify_all();
}

// Run the Haar detector on whichever frame the capture thread last handed over
void FaceTracker::detectThreadFunction() {
    while (!shouldQuit) {
        // Wait for the next frame to look at
        cv::Mat frame;
        {
            unique_lock<mutex> detectLock(detectMutex);
            detectReady.wait(detectLock, [this] { return detectBusy || shouldQuit.load(); });
            if (shouldQuit)
                return;
            frame = detectFrame;
        }

        // Keep the largest face by area, and the boxes the preview draws
        vector<cv::Rect> faces = detectFaces(frame);
        unique_lock<mutex> faceLock(faceMutex);
        lastFaces = faces;
        largestFace = 0;
        if (!faces.empty()) {
            int maxArea = 0;
            for (size_t i = 0; i < faces.size(); i++) {
                int area = faces[i].width * faces[i].height;
                if (area > maxArea) {
                    maxArea = area;
                    largestFace = i;
                    currentFace = faces[largestFace];
                }
            }
        } else {
            currentFace = cv::Rect();
        }
        faceLock.unlock();

        // Say the detector is free for the next frame
        unique_lock<mutex> detectLock(detectMutex);
        detectBusy = false;
    }
}

// Find every face in one frame
vector<cv::Rect> FaceTracker::detectFaces(const cv::Mat& frame) {
    vector<cv::Rect> faces;
    cv::Mat frame_gray;

    // Convert, blur, and equalize for more stable Haar detection
    cv::cvtColor(frame, frame_gray, cv::COLOR_BGR2GRAY);
    cv::GaussianBlur(frame_gray, frame_gray, cv::Size(DETECT_BLUR_SIZE, DETECT_BLUR_SIZE), 0);
    cv::equalizeHist(frame_gray, frame_gray);

    // Detect faces with a modest min size so distant faces still count
    cv::Size min_face = cv::Size(DETECT_MIN_FACE, DETECT_MIN_FACE);
    cv::Size max_face = cv::Size(DETECT_MAX_FACE, DETECT_MAX_FACE);
    face_cascade.detectMultiScale(frame_gray, faces, DETECT_SCALE_FACTOR, DETECT_MIN_NEIGHBORS, cv::CASCADE_SCALE_IMAGE, min_face, max_face);
    return faces;
}

// Report the tracked face as an offset from centre, from -1 to 1
bool FaceTracker::getFacePosition(float& x, float& y) {
    if (!cameraAvailable)
        return false;

    // Check if we have a valid face
    unique_lock<mutex> lock(faceMutex);
    if (currentFace.width == 0 || currentFace.height == 0)
        return false;

    // Calculate face position relative to frame center, then normalize
    float centerX = camera.width / 2.0f;
    float centerY = camera.height / 2.0f;
    x = (currentFace.x + currentFace.width / 2) - centerX;
    y = (currentFace.y + currentFace.height / 2) - centerY;
    x /= centerX;
    y /= centerY;

    // Flip both so a face to the left reads negative
    x = -x;
    y = -y;
    return true;
}

// Draw the camera preview over the face
void FaceTracker::updateWindow() {
    if (!showWindow.load() || !renderer)
        return;
    try {
        // Pull a new camera frame into the preview buffer when one is ready
        bool uploadFrame = false;
        cv::Mat frameCopy;
        {
            unique_lock<mutex> lock(frameMutex);
            if (hasNewFrame) {
                cv::flip(currentFrame, renderFrame, 1);
                cv::cvtColor(renderFrame, previewRgb, cv::COLOR_BGR2RGB);
                hasNewFrame = false;
                hasPreviewFrame = !previewRgb.empty();
                if (hasPreviewFrame) {
                    frameCopy = previewRgb.clone();
                    uploadFrame = true;
                }
            }
            if (!hasPreviewFrame)
                return;
        }

        // Create or resize the cached texture when the frame size changes
        if (uploadFrame) {
            if (!previewTexture || previewTextureWidth != frameCopy.cols || previewTextureHeight != frameCopy.rows) {
                if (previewTexture)
                    SDL_DestroyTexture(previewTexture);
                previewTexture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, frameCopy.cols, frameCopy.rows);
                previewTextureWidth = frameCopy.cols;
                previewTextureHeight = frameCopy.rows;
            }
            if (previewTexture)
                SDL_UpdateTexture(previewTexture, NULL, frameCopy.data, static_cast<int>(frameCopy.step));
        }
        if (!previewTexture)
            return;

        // Blit the last frame so SDL_RenderClear does not flash the preview white
        int previewHeight = (screen_width * previewTextureHeight) / previewTextureWidth;
        SDL_Rect previewRect = {0, PREVIEW_TOP, screen_width, previewHeight};
        SDL_RenderCopy(renderer, previewTexture, NULL, &previewRect);
    } catch (const exception& error) {
        cerr << "Error updating window: " << error.what() << endl;
    }
}

// Reopen the camera after a call handed it back
bool FaceTracker::initializeCamera() {
    if (cameraAvailable)
        return true;
    cameraAvailable = camera.initialize();
    return cameraAvailable;
}

// Hand the camera to another program
void FaceTracker::stopCamera() {
    stopTracking();
    cout << "Camera stopped" << endl;
}

// Stop the detection thread and close the camera
void FaceTracker::stopTracking() {
    // Close capture so a blocked frame read returns, then join
    shouldQuit = true;
    camera.release();
    cameraAvailable = false;
    detectReady.notify_all();
    if (trackingThread.joinable())
        trackingThread.join();
    if (detectThread.joinable())
        detectThread.join();
    tracking = false;
}

// Stop tracking and free the preview texture
FaceTracker::~FaceTracker() {
    stopTracking();

    // Free cached preview texture on the SDL thread
    if (previewTexture) {
        SDL_DestroyTexture(previewTexture);
        previewTexture = nullptr;
    }
}
