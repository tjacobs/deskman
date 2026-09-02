#include "tracker.hpp"
#include <iostream>
#include <filesystem>
#include "screen.h"

static const int FACE_DETECT_FPS = 3;
static const int PREVIEW_TOP = 120;

FaceTracker::FaceTracker(bool show_window, bool use_camera): showWindow(show_window) {

    // Try common OpenCV Haar cascade locations
    vector<string> possible_paths = {
        "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
        "/opt/homebrew/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
    };

    // Load the face detection classifier
    bool loaded = false;
    for (const auto& path: possible_paths) {
        if (filesystem::exists(path)) {
            if (face_cascade.load(path)) {
                loaded = true;
                break;
            }
        }
    }

    // Fail when the cascade file is missing
    if (!loaded) {
        cerr << "Error: Could not find or load face cascade classifier in any of the following paths:" << endl;
        for (const auto& path: possible_paths) {
            cerr << "  " << path << endl;
        }
        throw runtime_error("Failed to load face cascade classifier");
    }

    // Open the camera unless tracking is disabled
    if (use_camera) {
        cameraAvailable = camera.initialize();
        if (!cameraAvailable) {
            showWindow = false;
        }
    }

    // Preview is drawn on the main SDL display when showWindow is set
    if (showWindow) {
        cout << "Camera preview enabled on main display" << endl;
    }
}

FaceTracker::~FaceTracker() {
    stopTracking();

    // Free cached preview texture on the SDL thread
    if (previewTexture) {
        SDL_DestroyTexture(previewTexture);
        previewTexture = nullptr;
    }
}

void FaceTracker::startTracking() {
    if (isTracking()) {
        return;
    }
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

void FaceTracker::stopTracking() {

    // Close capture so a blocked frame read returns, then join
    shouldQuit = true;
    camera.release();
    cameraAvailable = false;
    if (trackingThread.joinable()) trackingThread.join();
}

bool FaceTracker::initializeCamera() {
    if (cameraAvailable) {
        return true;
    }
    cameraAvailable = camera.initialize();
    if (!cameraAvailable) {
        return false;
    }
    cout << "Camera initialized successfully" << endl;
    return true;
}

void FaceTracker::stopCamera() {
    stopTracking();
    cout << "Camera stopped" << endl;
}

bool FaceTracker::getFacePosition(float& x, float& y) {
    if (!cameraAvailable) {
        return false;
    }

    // Check if we have a valid face
    unique_lock<mutex> lock(faceMutex);
    if (currentFace.width == 0 || currentFace.height == 0) {
        return false;
    }

    // Calculate face position relative to frame center, then normalize
    float centerX = camera.width / 2.0f;
    float centerY = camera.height / 2.0f;
    x = (currentFace.x + currentFace.width/2) - centerX;
    y = (currentFace.y + currentFace.height/2) - centerY;
    x /= centerX;
    y /= centerY;
    x = -x;
    y = -y;
    return true;
}

void FaceTracker::updateWindow() {
    if (!showWindow || !cameraAvailable || !renderer) return;

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
            if (!hasPreviewFrame) return;
        }

        // Create or resize the cached texture when the frame size changes
        if (uploadFrame) {
            if (!previewTexture || previewTextureWidth != frameCopy.cols || previewTextureHeight != frameCopy.rows) {
                if (previewTexture) SDL_DestroyTexture(previewTexture);
                previewTexture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, frameCopy.cols, frameCopy.rows);
                previewTextureWidth = frameCopy.cols;
                previewTextureHeight = frameCopy.rows;
            }
            if (previewTexture) {
                SDL_UpdateTexture(previewTexture, NULL, frameCopy.data, static_cast<int>(frameCopy.step));
            }
        }
        if (!previewTexture) return;

        // Blit the last frame so SDL_RenderClear does not flash the preview white
        int previewWidth = screen_width / 4;
        int previewHeight = (previewWidth * previewTextureHeight) / previewTextureWidth;
        int previewX = (screen_width - previewWidth) / 2;
        int previewY = PREVIEW_TOP;
        SDL_Rect previewRect = {previewX, previewY, previewWidth, previewHeight};
        SDL_RenderCopy(renderer, previewTexture, NULL, &previewRect);
    } catch (const exception& error) {
        cerr << "Error updating window: " << error.what() << endl;
    }
}

void FaceTracker::trackingThreadFunction() {
    try {
        while (!shouldQuit) {

            // Capture frame from camera
            cv::Mat frame;
            if (!camera.captureFrame(frame)) {
                cerr << "Error: Could not read frame from camera" << endl;
                break;
            }

            // Detect faces in current frame
            auto faces = detectFaces(frame);

            // Keep the largest face by area
            unique_lock<mutex> lock(faceMutex);
            size_t largestFaceIdx = 0;
            if (!faces.empty()) {
                int maxArea = 0;
                for (size_t i = 0; i < faces.size(); i++) {
                    int area = faces[i].width * faces[i].height;
                    if (area > maxArea) {
                        maxArea = area;
                        largestFaceIdx = i;
                        currentFace = faces[largestFaceIdx];
                    }
                }
            } else {
                currentFace = cv::Rect();
            }
            lock.unlock();

            // Draw faces and update frame buffer if window is enabled
            if (showWindow) {
                for (size_t i = 0; i < faces.size(); i++) {
                    cv::Scalar color = (i == largestFaceIdx) ? cv::Scalar(0, 255, 0) : cv::Scalar(128, 128, 128);
                    cv::rectangle(frame, faces[i], color, 2);
                }
                unique_lock<mutex> frameLock(frameMutex);
                frame.copyTo(currentFrame);
                hasNewFrame = true;
                frameLock.unlock();
            }

            // Sleep for the Haar rate, not the camera rate
            const int sleep_ms = 1000 / FACE_DETECT_FPS;
            this_thread::sleep_for(chrono::milliseconds(sleep_ms));
        }
    } catch (const exception& error) {
        cerr << "Face tracking error: " << error.what() << endl;
    }
}

vector<cv::Rect> FaceTracker::detectFaces(const cv::Mat& frame) {
    vector<cv::Rect> faces;
    cv::Mat frame_gray;

    // Convert, blur, and equalize for more stable Haar detection
    cv::cvtColor(frame, frame_gray, cv::COLOR_BGR2GRAY);
    cv::GaussianBlur(frame_gray, frame_gray, cv::Size(5, 5), 0);
    cv::equalizeHist(frame_gray, frame_gray);

    // Detect faces with a modest min size so distant faces still count
    face_cascade.detectMultiScale(frame_gray, faces, 1.1, 3, cv::CASCADE_SCALE_IMAGE, cv::Size(20, 20), cv::Size(300, 300));
    return faces;
}
