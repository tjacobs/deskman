// Local
#include "tracker.h"
#include "screen.h"

// System
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>

// Namespace
using namespace std;

// Run the Haar detector well under the camera rate, it is the expensive part
static const int FACE_DETECT_FPS = 3;
static const int FACE_DETECT_SLEEP_MS = 1000 / FACE_DETECT_FPS;

// Preview sits below the top of the screen, at a fraction of its width
static const int PREVIEW_TOP = 120;
static const int PREVIEW_WIDTH_DIVISOR = 4;

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

    // Preview is drawn on the main SDL display when showWindow is set
    if (showWindow)
        cout << "Camera preview enabled on main display" << endl;
}

// Start the detection thread, unless it is already running
void FaceTracker::startTracking() {
    if (isTracking())
        return;
    cout << "Starting camera..." << endl;

    // Only start tracking if camera is available
    if (!cameraAvailable) {
        cout << "Face tracking disabled - camera not available" << endl;
        return;
    }

    // Start tracking thread
    shouldQuit = false;
    trackingThread = thread(&FaceTracker::trackingThreadFunction, this);
}

// Follow the largest face in each frame until stopTracking
void FaceTracker::trackingThreadFunction() {
    try {
        while (!shouldQuit) {
            // Capture frame from camera
            cv::Mat frame;
            if (!camera.captureFrame(frame)) {
                cerr << "Error: Could not read frame from camera" << endl;
                break;
            }

            // Keep the largest face by area
            auto faces = detectFaces(frame);
            unique_lock<mutex> lock(faceMutex);
            size_t largestFaceIndex = 0;
            if (!faces.empty()) {
                int maxArea = 0;
                for (size_t i = 0; i < faces.size(); i++) {
                    int area = faces[i].width * faces[i].height;
                    if (area > maxArea) {
                        maxArea = area;
                        largestFaceIndex = i;
                        currentFace = faces[largestFaceIndex];
                    }
                }
            } else {
                currentFace = cv::Rect();
            }
            lock.unlock();

            // Draw faces and update frame buffer if window is enabled
            if (showWindow) {
                for (size_t i = 0; i < faces.size(); i++) {
                    cv::Scalar color = i == largestFaceIndex ? TRACKED_FACE_COLOR : OTHER_FACE_COLOR;
                    cv::rectangle(frame, faces[i], color, FACE_BOX_THICKNESS);
                }
                unique_lock<mutex> frameLock(frameMutex);
                frame.copyTo(currentFrame);
                hasNewFrame = true;
                frameLock.unlock();
            }

            // Sleep for the Haar rate, not the camera rate
            this_thread::sleep_for(chrono::milliseconds(FACE_DETECT_SLEEP_MS));
        }
    } catch (const exception& error) {
        cerr << "Face tracking error: " << error.what() << endl;
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
    if (!showWindow || !cameraAvailable || !renderer)
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
        int previewWidth = screen_width / PREVIEW_WIDTH_DIVISOR;
        int previewHeight = (previewWidth * previewTextureHeight) / previewTextureWidth;
        int previewX = (screen_width - previewWidth) / 2;
        SDL_Rect previewRect = {previewX, PREVIEW_TOP, previewWidth, previewHeight};
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
    if (!cameraAvailable)
        return false;
    cout << "Camera initialized successfully" << endl;
    return true;
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
    if (trackingThread.joinable())
        trackingThread.join();
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
