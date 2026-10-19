// Local
#include "tracker.h"
#include "screen.h"
#include "recorder.h"

// System
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <string>

// Namespace
using namespace std;

// Run the face detector well under the camera rate, it is the expensive part
static const int FACE_DETECT_FPS = 10;
static const int FACE_DETECT_GAP_MS = 1000 / FACE_DETECT_FPS;

// Look this often while no face is in view, so an empty room costs little
static const int EMPTY_DETECT_FPS = 1;
static const int EMPTY_DETECT_GAP_MS = 1000 / EMPTY_DETECT_FPS;

// How long to wait when the recording has not sent its next frame yet
static const int FRAME_WAIT_MS = 10;

// Pace the preview, reading the camera flat out cooks the Pi for frames nobody sees
static const int PREVIEW_FPS = 30;
static const int PREVIEW_GAP_MS = 1000 / PREVIEW_FPS;

// Turn on to log how many camera frames a second actually reach the screen
static const bool LOG_PREVIEW_FPS = false;
static const int LOG_FPS_GAP_MS = 5000;

// Turn on to draw the face feature dots and the head turn arrow on the preview, off keeps just the boxes
static const bool SHOW_FACE_FEATURES = false;

// Green box on the face being followed, grey on the rest
static const cv::Scalar TRACKED_FACE_COLOR = cv::Scalar(0, 255, 0);
static const cv::Scalar OTHER_FACE_COLOR = cv::Scalar(128, 128, 128);
static const int FACE_BOX_THICKNESS = 2;

// Dots on each face, one colour each for the eyes, the nose, and the mouth corners
static const cv::Scalar LANDMARK_EYE_COLOR = cv::Scalar(255, 170, 60);
static const cv::Scalar LANDMARK_NOSE_COLOR = cv::Scalar(0, 215, 255);
static const cv::Scalar LANDMARK_MOUTH_COLOR = cv::Scalar(147, 112, 255);
static const cv::Scalar LANDMARK_COLORS[LANDMARK_COUNT] = {LANDMARK_EYE_COLOR, LANDMARK_EYE_COLOR, LANDMARK_NOSE_COLOR, LANDMARK_MOUTH_COLOR, LANDMARK_MOUTH_COLOR};
static const int LANDMARK_RADIUS = 3;

// Arrow from the nose showing which way the head is turned, as a share of the face width per unit of turn
static const cv::Scalar HEAD_ARROW_COLOR = cv::Scalar(0, 170, 255);
static const int HEAD_ARROW_THICKNESS = 2;
static const float HEAD_ARROW_LENGTH = 4.0f;

// Where the nose sits from the eyes down to the mouth when looking straight at the camera
static const float NOSE_LEVEL_FACING = 0.6f;

// Where the landmarks start in each row the detector returns, after the box and before the score
static const int LANDMARK_FIRST_COLUMN = 4;

// Detect on a frame shrunk to this width, a small frame is much cheaper and finds the same faces
static const int DETECT_WIDTH = 320;

// Face detector model beside the sources, and how sure it must be to call something a face
static const char* DETECT_MODEL_NAME = "face_detection_yunet_2023mar.onnx";
static const float DETECT_MIN_SCORE = 0.6f;
static const float DETECT_OVERLAP_LIMIT = 0.3f;
static const int DETECT_MAX_FACES = 50;

// Limit how many threads OpenCV uses, its worker pool spins between frames and costs more CPU than the detector itself
static const int OPENCV_THREADS = 1;

// Forward declarations
static void logPreviewRate();
static void drawHeadArrow(cv::Mat& frame, const FaceLandmarks& landmarks, const cv::Rect& face);

// Load the face detector and open the camera
FaceTracker::FaceTracker(bool show_window, bool use_camera): showWindow(show_window) {
    // Fail when the model file is missing
    string model_path = string(ROBOT_MODEL_DIR) + "/" + DETECT_MODEL_NAME;
    if (!filesystem::exists(model_path))
        throw runtime_error("Could not find the face detector model " + model_path);

    // Keep OpenCV's worker pool from spinning
    cv::setNumThreads(OPENCV_THREADS);

    // Load the model, sized for the shrunk frames
    face_detector = cv::FaceDetectorYN::create(model_path, "", cv::Size(DETECT_WIDTH, DETECT_WIDTH), DETECT_MIN_SCORE, DETECT_OVERLAP_LIMIT, DETECT_MAX_FACES);

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

            // Look often while a face is in view, and rarely in an empty room
            unique_lock<mutex> gapLock(faceMutex);
            int detectGap = lastFaces.empty() ? EMPTY_DETECT_GAP_MS : FACE_DETECT_GAP_MS;
            gapLock.unlock();

            // Hand the frame to the detector on a timer, it runs alongside so it never holds up the preview
            auto now = chrono::steady_clock::now();
            if (chrono::duration_cast<chrono::milliseconds>(now - lastDetectAt).count() >= detectGap) {
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
                vector<FaceLandmarks> landmarks = lastLandmarks;
                size_t largest = largestFace;
                faceLock.unlock();
                for (size_t i = 0; i < faces.size(); i++) {
                    cv::Scalar color = i == largest ? TRACKED_FACE_COLOR : OTHER_FACE_COLOR;
                    cv::rectangle(frame, faces[i], color, FACE_BOX_THICKNESS);

                    // Add the feature dots and head arrow when asked for
                    if (!SHOW_FACE_FEATURES)
                        continue;
                    for (int point = 0; point < LANDMARK_COUNT; point++)
                        cv::circle(frame, landmarks[i][point], LANDMARK_RADIUS, LANDMARK_COLORS[point], cv::FILLED);
                    drawHeadArrow(frame, landmarks[i], faces[i]);
                }
                unique_lock<mutex> frameLock(frameMutex);
                frame.copyTo(currentFrame);
                hasNewFrame = true;
                frameLock.unlock();
            }

            // Hold the loop to the preview rate, with nothing on screen only the detector needs frames
            int gap = showWindow.load() ? PREVIEW_GAP_MS : detectGap;
            auto spent = chrono::duration_cast<chrono::milliseconds>(chrono::steady_clock::now() - frameStart).count();
            if (spent < gap)
                this_thread::sleep_for(chrono::milliseconds(gap - spent));
        }
    } catch (const exception& error) {
        cerr << "Face tracking error: " << error.what() << endl;
    }

    // Let startTracking run again after the thread stops on its own
    tracking = false;
    detectReady.notify_all();
}

// Run the face detector on whichever frame the capture thread last handed over
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
        vector<FaceLandmarks> landmarks;
        vector<cv::Rect> faces = detectFaces(frame, landmarks);
        unique_lock<mutex> faceLock(faceMutex);
        lastFaces = faces;
        lastLandmarks = landmarks;
        frameWidth = frame.cols;
        frameHeight = frame.rows;
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

// Find every face in one frame, shrunk first so it costs little
vector<cv::Rect> FaceTracker::detectFaces(const cv::Mat& frame, vector<FaceLandmarks>& landmarks) {
    // Shrink the frame, keeping its shape
    double scale = (double)DETECT_WIDTH / frame.cols;
    cv::Mat small;
    cv::resize(frame, small, cv::Size(DETECT_WIDTH, (int)round(frame.rows * scale)), 0, 0, cv::INTER_AREA);

    // Detect, then scale the boxes back up to the full frame
    cv::Mat found;
    face_detector->setInputSize(small.size());
    face_detector->detect(small, found);
    vector<cv::Rect> faces;
    for (int row = 0; row < found.rows; row++) {
        cv::Rect box((int)(found.at<float>(row, 0) / scale), (int)(found.at<float>(row, 1) / scale), (int)(found.at<float>(row, 2) / scale), (int)(found.at<float>(row, 3) / scale));
        faces.push_back(box & cv::Rect(0, 0, frame.cols, frame.rows));

        // Scale the landmarks the same way
        FaceLandmarks points;
        for (int point = 0; point < LANDMARK_COUNT; point++)
            points[point] = cv::Point((int)(found.at<float>(row, LANDMARK_FIRST_COLUMN + point * 2) / scale), (int)(found.at<float>(row, LANDMARK_FIRST_COLUMN + point * 2 + 1) / scale));
        landmarks.push_back(points);
    }
    return faces;
}

// Draw an arrow from the nose toward where the head is turned, guessed from the nose against the eyes and mouth
static void drawHeadArrow(cv::Mat& frame, const FaceLandmarks& landmarks, const cv::Rect& face) {
    // Measure the eyes and mouth, skipping a face too small or too odd to read
    cv::Point2f eyeMiddle = (cv::Point2f(landmarks[0]) + cv::Point2f(landmarks[1])) / 2.0f;
    cv::Point2f mouthMiddle = (cv::Point2f(landmarks[3]) + cv::Point2f(landmarks[4])) / 2.0f;
    float eyeGap = fabs((float)(landmarks[1].x - landmarks[0].x));
    float eyeToMouth = mouthMiddle.y - eyeMiddle.y;
    if (eyeGap < 1.0f || eyeToMouth < 1.0f)
        return;

    // Sideways turn moves the nose across the eyes, up and down moves it along the face
    float turnX = (landmarks[2].x - eyeMiddle.x) / eyeGap;
    float turnY = (landmarks[2].y - eyeMiddle.y) / eyeToMouth - NOSE_LEVEL_FACING;

    // Point the arrow that way, as long as the face is wide
    cv::Point2f tip = cv::Point2f(landmarks[2]) + cv::Point2f(turnX, turnY) * (face.width * HEAD_ARROW_LENGTH);
    cv::arrowedLine(frame, landmarks[2], cv::Point(tip), HEAD_ARROW_COLOR, HEAD_ARROW_THICKNESS);
}

// Report the tracked face as an offset from centre, from -1 to 1
bool FaceTracker::getFacePosition(float& x, float& y) {
    // A recording feeds the frames when the camera has been handed over
    if (!cameraAvailable && !recording())
        return false;

    // Check if we have a valid face
    unique_lock<mutex> lock(faceMutex);
    if (currentFace.width == 0 || currentFace.height == 0 || frameWidth == 0 || frameHeight == 0)
        return false;

    // Measure against the frames being tracked on, a recording pipes a different shape back
    float centerX = frameWidth / 2.0f;
    float centerY = frameHeight / 2.0f;
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

            // Count the camera frames that made it onto the screen
            if (LOG_PREVIEW_FPS)
                logPreviewRate();
        }
        if (!previewTexture)
            return;

        // Blit the last frame so SDL_RenderClear does not flash the preview white
        int top = previewTop();
        SDL_Rect previewRect = {0, top, screen_width, screen_height - status_bar_height() - top};
        SDL_RenderCopy(renderer, previewTexture, NULL, &previewRect);
    } catch (const exception& error) {
        cerr << "Error updating window: " << error.what() << endl;
    }
}

// Top edge of the preview, sized to the screen width and sitting on the status bar
int FaceTracker::previewTop() const {
    if (!previewTexture || previewTextureWidth == 0)
        return -1;
    int previewHeight = (screen_width * previewTextureHeight) / previewTextureWidth;
    return screen_height - status_bar_height() - previewHeight;
}

// Print how many camera frames a second the screen is showing
static void logPreviewRate() {
    static int frames = 0;
    static auto since = chrono::steady_clock::now();
    frames++;

    // Report on the interval, then start counting again
    auto spent = chrono::duration_cast<chrono::milliseconds>(chrono::steady_clock::now() - since).count();
    if (spent < LOG_FPS_GAP_MS)
        return;
    printf("Preview FPS: %.1f\n", frames * 1000.0 / spent);
    frames = 0;
    since = chrono::steady_clock::now();
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
