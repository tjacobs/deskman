/*
 * Video
 *
*/

// Includes
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <climits>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

#ifdef __linux__
#include <fcntl.h>
#include <linux/videodev2.h>
#include <pthread.h>
#include <sys/ioctl.h>
#include <unistd.h>
#endif

// Local
#include "video.h"
#include "audio.h"
#include "call.h"
#include "interface.h"

// Namespace
using namespace std;
using namespace ix;

// Video needs GStreamer WebRTC, skip the rest of the file without it so this is the only error
#ifndef HAVE_GSTREAMER_WEBRTC
#error "GStreamer WebRTC not found. Run teleport/install.sh to install it."
#else

#ifdef HAVE_GSTREAMER_WEBRTC

// GStreamer
#include <gst/gst.h>
#include <gst/sdp/sdp.h>
#include <gst/video/video.h>
#include <gst/webrtc/webrtc.h>
#include <json-glib/json-glib.h>

#ifdef HAVE_X11_FULLSCREEN
#include <X11/Xatom.h>
#include <X11/Xlib.h>
#endif

#endif

// Track video state
WebSocket* videoWebSocket = NULL;
bool videoRunning = false;
bool dialedCall = false;
bool createOfferPending = false;
bool includeAudioInPipeline = false;
string activeCameraPath;
string activeCameraView;
string videoDeviceName;
const char* CAMERA_VIEW_LEFT = "left";
const char* CAMERA_VIEW_RIGHT = "right";
const char* CAMERA_VIEW_FULL = "full";

// Declare shared functions
string getCameraView(string cameraView);

#ifdef HAVE_GSTREAMER_WEBRTC

// The encoder only makes constrained baseline, so declare that until an offer names another
const char* CONSTRAINED_BASELINE_PROFILE = "42e01f";

// Candidates wait here for a remote description, through ringing and pipeline rebuilds, until the call ends
vector<string> pendingVideoAddresses;

// Track GStreamer state
GMainLoop* videoLoop = NULL;
GstElement* videoPipeline = NULL;
GstElement* videoWebrtc = NULL;
thread videoLoopThread;
bool videoBackendReady = false;
bool videoCameraAvailable = true;
int videoPayloadType = 103;
string videoProfileLevelId = CONSTRAINED_BASELINE_PROFILE;
bool remoteDescriptionSet = false;
guint iceDisconnectTimerId = 0;
int iceDisconnectSessionId = 0;
int videoSessionId = 0;
gint64 remoteVideoBufferTimeUs = 0;
guint remoteVideoTimerId = 0;
bool remoteVideoShowing = false;
GstElement* remoteVideoSink = NULL;
int remoteVideoWidth = 0;
int remoteVideoHeight = 0;
const int ICE_DISCONNECT_HANGOVER_MS = 3000;
const int REMOTE_VIDEO_IDLE_MS = 1500;
const int REMOTE_VIDEO_POLL_MS = 500;
#ifdef HAVE_X11_FULLSCREEN
Display* videoDisplay = NULL;
Window videoWindow = 0;
const long NET_WM_STATE_ADD = 1;
#endif
const int VIDEO_WIDTH = 1920;
const int VIDEO_HEIGHT = 1080;
const int VIDEO_PORTRAIT_WIDTH_RATIO = 9;
const int VIDEO_PORTRAIT_HEIGHT_RATIO = 16;
const int VIDEO_FRAMERATE = 30;
const int VIDEO_BITRATE = 2000;
const int LIBCAMERA_WIDTH = VIDEO_WIDTH;
const int LIBCAMERA_HEIGHT = VIDEO_HEIGHT;
const int LIBCAMERA_FRAMERATE = 30;
const int VIDEO_STATE_TIMEOUT_SECONDS = 2;
const int SENDER_CAPS_WAIT_MS = 8000;
const int SENDER_CAPS_POLL_MS = 50;
const int VIDEO_CLOCK_RATE = 90000;
const int AUDIO_CLOCK_RATE = 48000;
const char* VIDEO_DEFAULT_CAMERA_PATH = "0";
const char* VIDEO_PREFERRED_CAMERA_PATH = "/dev/video6";
const int VIDEO_MAX_INDEX = 8;

// Describe camera capture format
struct CameraFormat {
    bool available;
    string caps;
    string name;
    int width;
    int height;
    int framerate;
    int widthDistance;
    int heightDistance;
    int framerateDistance;
};

// Declare functions
bool initVideoBackend();
bool isVideoCameraAvailable();
void runVideoLoop();
void startVideoPipeline();
bool startParsedVideoPipeline(string pipelineString);
string createPipelineString(bool withAudio);
string createVideoSourceString();
string createVideoProfileString();
string getCameraDevice();
CameraFormat getCameraFormat();
string createCameraCropString(CameraFormat cameraFormat);
CameraFormat makeDefaultCameraFormat();
string getVideoEncoderString();
bool isVideoHardwareEncoderAvailable();
bool isGStreamerElementAvailable(string name);
#ifdef __linux__
void evaluateCameraFrameSize(int fileDescriptor, __u32 pixelFormat, string caps, string name, CameraFormat& bestFormat);
bool isBetterCameraFormat(CameraFormat candidateFormat, CameraFormat bestFormat);
int getIntervalFramerate(v4l2_fract interval);
string getCameraPixelCaps(__u32 pixelFormat);
string getCameraPixelName(__u32 pixelFormat);
#endif
void printCameraFormats();
string shellQuote(string value);
void stopVideoPipeline();
void handleVideoOffer(string payload);
void handleVideoAnswer(string payload);
void handleVideoAddress(string payload);
void applyPendingVideoAddresses();
void createVideoOffer();
void chooseVideoCodec(const char* sdpText);
void chooseAudioCodec(const char* sdpText);
int findPayloadType(string sdp, string codec);
string summarizeSdp(const char* sdpText);
void logGStreamerError(const string& message);
void waitForSenderCaps();
void prepareSenderTransceivers();
void prepareSenderTransceiver(int index, string media, string codec, int payloadType, GstWebRTCRTPTransceiverDirection direction);
void applyRemoteVideoOffer(const char* sdpText);
void sendVideoDescription(GstWebRTCSessionDescription* description);
void sendVideoAddress(GstElement* element, guint mediaLineIndex, gchar* candidate, gpointer userData);
void onIncomingStream(GstElement* element, GstPad* pad, gpointer userData);
void onDecodedStream(GstElement* decodebin, GstPad* pad, gpointer userData);
bool remoteSinkKeepsAspect(GstElement* sink);
void applyRemoteVideoLetterbox();
GstPadProbeReturn onRemoteVideoCaps(GstPad* pad, GstPadProbeInfo* info, gpointer userData);
gboolean handleVideoBusMessage(GstBus* bus, GstMessage* message, gpointer userData);
GstBusSyncReply handleVideoBusSync(GstBus* bus, GstMessage* message, gpointer userData);
GstPadProbeReturn logFirstCameraBuffer(GstPad* pad, GstPadProbeInfo* info, gpointer userData);
gboolean stopVideoFromMainLoop(gpointer userData);
void logVideoConnectionState(GObject* object, GParamSpec* spec, gpointer userData);
#ifdef HAVE_X11_FULLSCREEN
Window ensureFullscreenVideoWindow();
void requestFullscreenVideoWindow();
void destroyFullscreenVideoWindow();
#endif
void startRemoteVideoIdleTimer();
GstPadProbeReturn noteRemoteVideoBuffer(GstPad* pad, GstPadProbeInfo* info, gpointer userData);
gboolean hideRemoteVideoWhenIdle(gpointer userData);
void showRemoteVideoWindow();
void hideRemoteVideoWindow();
void logVideoIceConnectionState(GObject* object, GParamSpec* spec, gpointer userData);
gboolean iceDisconnectHangup(gpointer userData);
void cancelIceDisconnectTimer();
void logVideoSignalingState(GObject* object, GParamSpec* spec, gpointer userData);
void onVideoOfferSet(GstPromise* promise, gpointer userData);
void onVideoAnswerCreated(GstPromise* promise, gpointer userData);
void onVideoOfferCreated(GstPromise* promise, gpointer userData);
void onVideoAnswerSet(GstPromise* promise, gpointer userData);
const char* videoConnectionStateName(GstWebRTCPeerConnectionState state);
const char* videoConnectionStateName(GstWebRTCICEConnectionState state);
const char* videoSignalingStateName(GstWebRTCSignalingState state);
string encodeJson(JsonObject* object);
JsonObject* decodeJson(string payload);
bool videoFileExists(string path);

#endif

// Initialize video
void initVideoCamera(WebSocket* webSocketPointer, string deviceNameValue) {
    // Store websocket
    videoWebSocket = webSocketPointer;
    videoDeviceName = deviceNameValue;
}

// Check video camera
void checkVideoCamera(string cameraPath) {
    // Save camera path
    activeCameraPath = cameraPath;

#ifdef HAVE_GSTREAMER_WEBRTC
    // Check device presence only, starting the pipeline here races libcamerasrc
    if (!initVideoBackend()) return;
    if (isGStreamerElementAvailable("libcamerasrc")) {
        videoCameraAvailable = true;
        return;
    }
    isVideoCameraAvailable();
#else
    // Log missing backend
    cout << "GStreamer WebRTC backend is not compiled in." << endl;
#endif
}

// Start video
void startVideo(string cameraPath, string cameraView) {
    // Keep the overlay in-call first so the idle loop does not stop this start
    showCallInProgress();

    // Keep the live sender, the web client and server both repeat StartVideo
    if (videoRunning && videoPipeline) return;

    // Save camera settings for the answering path, and send the mic so the caller can hear
    activeCameraPath = cameraPath;
    activeCameraView = getCameraView(cameraView);
    dialedCall = false;
    createOfferPending = false;
    includeAudioInPipeline = true;
    videoRunning = true;

#ifdef HAVE_GSTREAMER_WEBRTC
    // Wait for the browser offer so the H264 payload type matches before encoding
    cout << "StartVideo: waiting for offer, view=" << activeCameraView << endl;
#else
    // Log missing backend
    cout << "GStreamer WebRTC backend is not compiled in." << endl;
#endif
}

// Dial a peer from the screen, incoming calls are answered by startVideo instead
bool startCall(string cameraPath) {
    // Ignore a second tap on a peer we are already calling
    if (videoRunning && dialedCall) {
        cout << "Already in a call." << endl;
        return true;
    }

    // Save call settings
    activeCameraPath = cameraPath;
    activeCameraView = CAMERA_VIEW_FULL;
    dialedCall = true;
    createOfferPending = true;
    includeAudioInPipeline = true;
    videoRunning = true;

#ifdef HAVE_GSTREAMER_WEBRTC
    // Open the camera now, the offer follows once it is really running
    cout << "Starting call as caller." << endl;
    startVideoPipeline();
    return videoRunning && dialedCall;
#else
    failCallWithoutOffer("Call not sent");
    return false;
#endif
}

// True when a web view or robot call is using the camera
bool isVideoRunning() {
    return videoRunning;
}

// Stop video
void stopVideo() {
    bool sendStop = videoRunning;
    videoRunning = false;
    dialedCall = false;
    createOfferPending = false;
    includeAudioInPipeline = false;

#ifdef HAVE_GSTREAMER_WEBRTC
    stopVideoPipeline();
    pendingVideoAddresses.clear();
#endif

    resumeInterfaceAfterCall();
    showCallIdle();

    if (sendStop) {
        cout << "Stopping WebRTC video." << endl;
        if (videoWebSocket) videoWebSocket->send(videoDeviceName + " VIDEO_STOP");
    }
}

// Play remote PCM on the USB speaker, from the web client or another deskman
void startRemoteMic() {
    cout << "StartMic: speaker ready for remote PCM." << endl;
    startRemotePCMPlayback();
}

// Stop playing remote PCM
void stopRemoteMic() {
    cout << "StopMic: muted remote microphone." << endl;
    stopRemotePCMPlayback();
}

// Handle video signaling
void handleVideoMessage(string command, string payload) {
    // Handle offer
    if (command == "VIDEO_OFFER") {
#ifdef HAVE_GSTREAMER_WEBRTC
        handleVideoOffer(payload);
#else
        cout << "Ignoring video offer, GStreamer WebRTC backend is not compiled in." << endl;
#endif
    }

    // Handle answer
    else if (command == "VIDEO_ANSWER") {
#ifdef HAVE_GSTREAMER_WEBRTC
        handleVideoAnswer(payload);
#endif
    }

    // Handle ICE candidate
    else if (command == "VIDEO_ADDRESS") {
#ifdef HAVE_GSTREAMER_WEBRTC
        handleVideoAddress(payload);
#endif
    }

    // Stop video
    else if (command == "VIDEO_STOP") {
        stopVideo();
    }
}

// Forget candidates queued for a call that never got answered
void clearVideoAddresses() {
#ifdef HAVE_GSTREAMER_WEBRTC
    pendingVideoAddresses.clear();
#endif
}

// Get requested camera view
string getCameraView(string cameraView) {
    if (cameraView == CAMERA_VIEW_LEFT || cameraView == CAMERA_VIEW_RIGHT || cameraView == CAMERA_VIEW_FULL) return cameraView;
    if (cameraView == "both") return CAMERA_VIEW_FULL;
    return CAMERA_VIEW_FULL;
}

#ifdef HAVE_GSTREAMER_WEBRTC

// Initialize video backend
bool initVideoBackend() {
    // Skip repeated init
    if (videoBackendReady) return true;

    // Initialize GStreamer
    GError* error = NULL;
    gboolean result = gst_init_check(NULL, NULL, &error);
    if (!result) {
        cout << "Failed to initialize GStreamer: " << error->message << endl;
        g_error_free(error);
        return false;
    }

    // Quiet expected audio setup noise
    quietCallAudioLog();

    // Check WebRTC ICE plugin
    if (!gst_element_factory_find("nicesrc")) {
        cout << "Missing GStreamer nice plugin. On macOS run: brew install libnice-gstreamer" << endl;
        cout << "On Linux run: sudo apt install gstreamer1.0-nice" << endl;
        return false;
    }

    // Start callback loop
    videoLoop = g_main_loop_new(NULL, FALSE);
    videoLoopThread = thread(runVideoLoop);
    videoLoopThread.detach();
    videoBackendReady = true;
    return true;
}

// Run video loop
void runVideoLoop() {
#ifdef __linux__
    // Name thread for htop
    pthread_setname_np(pthread_self(), "robot_video");
#endif

    // Run GStreamer callbacks
    g_main_loop_run(videoLoop);
}

// Stop the call without sending a VIDEO_OFFER
static void failCallWithoutOffer(string message) {
    cout << message << endl;
    showCallFailed(message);
    createOfferPending = false;
    videoRunning = false;
    dialedCall = false;
    includeAudioInPipeline = false;
}

// Start video pipeline
void startVideoPipeline() {
    // Initialize backend
    if (!initVideoBackend()) {
        failCallWithoutOffer("Call not sent");
        return;
    }

    // Check camera
    if (!isVideoCameraAvailable()) {
        failCallWithoutOffer("No camera, call not sent");
        return;
    }

    // Let the interface peer drop the camera and speaker before we open them
    pauseInterfaceForCall();

    // Stop old pipeline
    stopVideoPipeline();

    // Prefer sending the mic, and give up on it when ALSA fails
    if (includeAudioInPipeline && !startParsedVideoPipeline(createPipelineString(true))) {
        cout << "***WARNING***: Mic failed, continuing with video only." << endl;
        includeAudioInPipeline = false;
    }

    // Start without the mic, either by choice or after that failure
    if (!includeAudioInPipeline && !startParsedVideoPipeline(createPipelineString(false))) {
        resumeInterfaceAfterCall();
        showCallFailed("Call not sent");
        return;
    }

    // Caller creates the offer once the camera and mic are really running
    if (createOfferPending && videoWebrtc) {
        createOfferPending = false;
        waitForSenderCaps();
        createVideoOffer();
    }
    showCallInProgress();
}

// Parse, wire, and play a pipeline string
bool startParsedVideoPipeline(string pipelineString) {
    // Create pipeline
    GError* error = NULL;
    videoPipeline = gst_parse_launch(pipelineString.c_str(), &error);
    if (error) {
        cout << "Failed to start video pipeline: " << error->message << endl;
        printCameraFormats();
        g_error_free(error);
        videoPipeline = NULL;
        return false;
    }

    // Get WebRTC element
    videoWebrtc = gst_bin_get_by_name(GST_BIN(videoPipeline), "sendrecv");
    if (!videoWebrtc) {
        cout << "Failed to find WebRTC element." << endl;
        stopVideoPipeline();
        return false;
    }
    videoSessionId++;

    // Log pipeline messages, and catch prepare-window-handle for fullscreen
    GstBus* bus = gst_pipeline_get_bus(GST_PIPELINE(videoPipeline));
    gst_bus_add_watch(bus, handleVideoBusMessage, NULL);
    gst_bus_set_sync_handler(bus, handleVideoBusSync, NULL, NULL);
    gst_object_unref(bus);

    // Add AEC probe and half duplex before PLAYING
    attachCallAudio(videoPipeline, videoLoop);

    // Register signaling callbacks
    g_signal_connect(videoWebrtc, "on-ice-candidate", G_CALLBACK(sendVideoAddress), NULL);
    g_signal_connect(videoWebrtc, "notify::connection-state", G_CALLBACK(logVideoConnectionState), NULL);
    g_signal_connect(videoWebrtc, "notify::ice-connection-state", G_CALLBACK(logVideoIceConnectionState), NULL);
    g_signal_connect(videoWebrtc, "notify::signaling-state", G_CALLBACK(logVideoSignalingState), NULL);

    // Receive remote audio from the web client, and remote video on robot calls
    g_signal_connect(videoWebrtc, "pad-added", G_CALLBACK(onIncomingStream), NULL);

    // Start pipeline
    GstStateChangeReturn result = gst_element_set_state(videoPipeline, GST_STATE_PLAYING);
    if (result == GST_STATE_CHANGE_FAILURE) {
        cout << "Failed to play video pipeline." << endl;
        printCameraFormats();
        stopVideoPipeline();
        return false;
    }

    // Wait briefly so camera startup errors appear immediately
    GstState state;
    GstState pendingState;
    result = gst_element_get_state(videoPipeline, &state, &pendingState, VIDEO_STATE_TIMEOUT_SECONDS * GST_SECOND);
    if (result == GST_STATE_CHANGE_FAILURE) {
        cout << "Failed while starting video pipeline." << endl;
        printCameraFormats();
        stopVideoPipeline();
        return false;
    }

    // Pipeline is running
    cout << "Video pipeline playing." << endl;

    // Log the first camera packet into webrtcbin
    GstPad* webrtcSink = gst_element_get_static_pad(videoWebrtc, "sink_0");
    if (webrtcSink) {
        gst_pad_add_probe(webrtcSink, GST_PAD_PROBE_TYPE_BUFFER, logFirstCameraBuffer, NULL, NULL);
        gst_object_unref(webrtcSink);
    }
    else {
        cout << "No webrtcbin sink_0 yet." << endl;
    }
    return true;
}

// Check if video camera is available
bool isVideoCameraAvailable() {
#ifdef __APPLE__
    // Assume AVFoundation can resolve camera index
    videoCameraAvailable = true;
    return true;
#else
    // Check V4L2 camera path
    string cameraDevice = getCameraDevice();
    cout << "Call camera:  " << cameraDevice << endl;
    videoCameraAvailable = videoFileExists(cameraDevice);
    if (!videoCameraAvailable) cout << "***WARNING***: No camera connected at " << cameraDevice << "." << endl;
    return videoCameraAvailable;
#endif
}

// Create pipeline string
string createPipelineString(bool withAudio) {
    // Build webrtcbin with local video, and optional mic audio
    string pipeline = "webrtcbin bundle-policy=max-bundle name=sendrecv latency=20 stun-server=stun://stun.l.google.com:19302 "
        + createVideoSourceString();
    if (withAudio) pipeline += " " + createAudioSourceString();
    return pipeline;
}

// Create local video source branch
string createVideoSourceString() {
#ifdef __APPLE__
    // Use Mac webcam
    string cameraIndex = activeCameraPath == "/dev/video0" ? "0" : activeCameraPath;
    string payloadType = to_string(videoPayloadType);
    string encoder = getVideoEncoderString();
    return "avfvideosrc name=robotcam device-index=" + cameraIndex + " ! videoconvert name=robotconvert ! " + encoder + " ! video/x-h264,profile=baseline ! h264parse name=robotparse ! rtph264pay name=robotpay config-interval=1 pt=" + payloadType + " ! application/x-rtp,media=video,encoding-name=H264,payload=" + payloadType + " ! sendrecv.";
#else
    string payloadType = to_string(videoPayloadType);
    string encoder = getVideoEncoderString();

    // Pi cameras use libcamera; discrete V4L2 JPEG is unavailable on Pi 5
    if (isGStreamerElementAvailable("libcamerasrc")) {
        CameraFormat cameraFormat = {true, "video/x-raw", "libcamera", LIBCAMERA_WIDTH, LIBCAMERA_HEIGHT, LIBCAMERA_FRAMERATE, 0, 0, 0};
        string cameraCrop = createCameraCropString(cameraFormat);
        cout << "Using libcamerasrc for WebRTC video at " << LIBCAMERA_WIDTH << "x" << LIBCAMERA_HEIGHT << "." << endl;
        return "libcamerasrc name=robotcam ! video/x-raw,format=RGB,width=" + to_string(LIBCAMERA_WIDTH) + ",height=" + to_string(LIBCAMERA_HEIGHT) + ",framerate=" + to_string(LIBCAMERA_FRAMERATE) + "/1 ! "
            + cameraCrop +
            "videoconvert name=robotconvert ! " + encoder + " ! "
            "h264parse name=robotparse ! rtph264pay name=robotpay config-interval=1 pt=" + payloadType + " ! "
            "application/x-rtp,media=video,encoding-name=H264,payload=" + payloadType + createVideoProfileString() + " ! sendrecv.";
    }

    // Use MJPEG or YUYV V4L2 camera and encode H264
    string cameraDevice = getCameraDevice();
    CameraFormat cameraFormat = getCameraFormat();
    string cameraCrop = createCameraCropString(cameraFormat);
    string decoder = cameraFormat.caps == "image/jpeg" ? "jpegdec name=robotjpeg ! " : "";
    return "v4l2src name=robotcam device=" + cameraDevice + " ! " + cameraFormat.caps + ",width=" + to_string(cameraFormat.width) + ",height=" + to_string(cameraFormat.height) + ",framerate=" + to_string(cameraFormat.framerate) + "/1 ! " + decoder + cameraCrop + "videoconvert name=robotconvert ! " + encoder + " ! h264parse name=robotparse ! rtph264pay name=robotpay config-interval=1 pt=" + payloadType + " ! application/x-rtp,media=video,encoding-name=H264,payload=" + payloadType + createVideoProfileString() + " ! sendrecv.";
#endif
}

// Declare the profile the peer offered, x264enc says 42c01f and webrtcbin then finds no match
string createVideoProfileString() {
    return " ! capssetter name=robotprofile caps=\"application/x-rtp,profile-level-id=(string)" + videoProfileLevelId + "\"";
}

// Get camera device path
string getCameraDevice() {
#ifndef __APPLE__
    // Prefer video6, then the first present /dev/videoN, when using the default camera
    if (activeCameraPath == VIDEO_DEFAULT_CAMERA_PATH) {
        if (videoFileExists(VIDEO_PREFERRED_CAMERA_PATH)) return VIDEO_PREFERRED_CAMERA_PATH;
        for (int index = 0; index <= VIDEO_MAX_INDEX; index++) {
            string path = "/dev/video" + to_string(index);
            if (videoFileExists(path)) return path;
        }
    }
#endif

    // Resolve numeric camera values
    return activeCameraPath.find("/") == string::npos ? "/dev/video" + activeCameraPath : activeCameraPath;
}

// Get closest supported camera format
CameraFormat getCameraFormat() {
    // Start with requested format
    CameraFormat bestFormat = makeDefaultCameraFormat();

#ifdef __linux__
    // Open camera device
    string cameraDevice = getCameraDevice();
    int fileDescriptor = open(cameraDevice.c_str(), O_RDONLY | O_NONBLOCK);
    if (fileDescriptor < 0) return bestFormat;

    // Enumerate pixel formats
    v4l2_fmtdesc pixelFormat;
    memset(&pixelFormat, 0, sizeof(pixelFormat));
    pixelFormat.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    while (ioctl(fileDescriptor, VIDIOC_ENUM_FMT, &pixelFormat) == 0) {
        string caps = getCameraPixelCaps(pixelFormat.pixelformat);
        string name = getCameraPixelName(pixelFormat.pixelformat);
        if (!caps.empty()) evaluateCameraFrameSize(fileDescriptor, pixelFormat.pixelformat, caps, name, bestFormat);
        pixelFormat.index++;
    }

    // Close camera device
    close(fileDescriptor);

    // Log selected format
    if (bestFormat.available) {
        cout << "Selected camera format: " << bestFormat.name << " " << bestFormat.width << "x" << bestFormat.height << " @" << bestFormat.framerate << " fps." << endl;
    } else {
        cout << "No suitable enumerated camera format found, requesting default format." << endl;
    }
#endif

    // Return selected format
    return bestFormat;
}

// Create camera crop pipeline
string createCameraCropString(CameraFormat cameraFormat) {
    int width = cameraFormat.width;
    int height = cameraFormat.height;
    string crop;

    // Crop side by side stereo frames only when the image is two views wide
    int cameraSideWidth = cameraFormat.width / 2;
    bool stereoFrame = cameraFormat.width >= cameraFormat.height * 2;
    if (stereoFrame && activeCameraView == CAMERA_VIEW_LEFT) {
        crop += "videocrop name=robotcrop right=" + to_string(cameraSideWidth) + " ! video/x-raw,width=" + to_string(cameraSideWidth) + ",height=" + to_string(height) + " ! ";
        width = cameraSideWidth;
    } else if (stereoFrame && activeCameraView == CAMERA_VIEW_RIGHT) {
        crop += "videocrop name=robotcrop left=" + to_string(cameraSideWidth) + " ! video/x-raw,width=" + to_string(cameraSideWidth) + ",height=" + to_string(height) + " ! ";
        width = cameraSideWidth;
    }

    // Center crop landscape frames to portrait, cameras do not offer a tall mode
    if (width > height) {
        int cropWidth = height * VIDEO_PORTRAIT_WIDTH_RATIO / VIDEO_PORTRAIT_HEIGHT_RATIO;
        cropWidth = cropWidth & ~1;
        if (cropWidth < 2) cropWidth = 2;
        if (cropWidth < width) {
            int extra = width - cropWidth;
            int left = (extra / 2) & ~1;
            int right = width - cropWidth - left;
            crop += "videocrop name=robotportrait left=" + to_string(left) + " right=" + to_string(right) + " ! video/x-raw,width=" + to_string(cropWidth) + ",height=" + to_string(height) + " ! ";
            cout << "Portrait crop " << cropWidth << "x" << height << " from " << width << "x" << height << endl;
        }
    }
    return crop;
}

// Make requested camera format
CameraFormat makeDefaultCameraFormat() {
    // Build default format
    CameraFormat format;
    format.available = false;
    format.caps = "image/jpeg";
    format.name = "MJPG";
    format.width = VIDEO_WIDTH;
    format.height = VIDEO_HEIGHT;
    format.framerate = VIDEO_FRAMERATE;
    format.widthDistance = INT_MAX;
    format.heightDistance = INT_MAX;
    format.framerateDistance = INT_MAX;
    return format;
}

// Get H264 encoder pipeline
string getVideoEncoderString() {
#ifdef __APPLE__
    // Use software H264 encoding on Mac
    cout << "Using software H264 encoder x264enc." << endl;
    return "x264enc name=robotenc tune=zerolatency speed-preset=ultrafast key-int-max=30 bitrate=" + to_string(VIDEO_BITRATE);
#else
    // Prefer Jetson hardware H264 encoding
    if (isVideoHardwareEncoderAvailable()) {
        cout << "Using hardware H264 encoder nvv4l2h264enc." << endl;
        return "video/x-raw,format=I420 ! nvvidconv name=robotnvconvert ! video/x-raw(memory:NVMM),format=NV12 ! nvv4l2h264enc name=robotenc insert-sps-pps=true iframeinterval=30 bitrate=" + to_string(VIDEO_BITRATE * 1000) + " ! video/x-h264,stream-format=byte-stream,profile=baseline";
    }

    // Prefer x264, then OpenH264 on boards without x264enc
    if (isGStreamerElementAvailable("x264enc")) {
        cout << "Using software H264 encoder x264enc." << endl;
        return "video/x-raw,format=I420 ! x264enc name=robotenc tune=zerolatency speed-preset=ultrafast key-int-max=30 bitrate=" + to_string(VIDEO_BITRATE) + " ! video/x-h264,profile=baseline";
    }
    cout << "Using software H264 encoder openh264enc." << endl;
    return "video/x-raw,format=I420 ! openh264enc name=robotenc bitrate=" + to_string(VIDEO_BITRATE * 1000) + " ! video/x-h264,profile=baseline";
#endif
}

// Check hardware encoder plugin
bool isVideoHardwareEncoderAvailable() {
#ifdef __linux__
    // Check Jetson H264 encoder elements
    return isGStreamerElementAvailable("nvv4l2h264enc") && isGStreamerElementAvailable("nvvidconv");
#else
    // Hardware encoder is only configured for Linux
    return false;
#endif
}

// Check GStreamer element
bool isGStreamerElementAvailable(string name) {
    // Query element factory
    GstElementFactory* factory = gst_element_factory_find(name.c_str());
    if (!factory) return false;
    gst_object_unref(factory);
    return true;
}

#ifdef __linux__

// Evaluate camera frame sizes
void evaluateCameraFrameSize(int fileDescriptor, __u32 pixelFormat, string caps, string name, CameraFormat& bestFormat) {
    // Enumerate frame sizes
    v4l2_frmsizeenum frameSize;
    memset(&frameSize, 0, sizeof(frameSize));
    frameSize.pixel_format = pixelFormat;
    while (ioctl(fileDescriptor, VIDIOC_ENUM_FRAMESIZES, &frameSize) == 0) {
        if (frameSize.type == V4L2_FRMSIZE_TYPE_DISCRETE) {
            // Enumerate frame intervals
            v4l2_frmivalenum frameInterval;
            memset(&frameInterval, 0, sizeof(frameInterval));
            frameInterval.pixel_format = pixelFormat;
            frameInterval.width = frameSize.discrete.width;
            frameInterval.height = frameSize.discrete.height;
            while (ioctl(fileDescriptor, VIDIOC_ENUM_FRAMEINTERVALS, &frameInterval) == 0) {
                int framerate = frameInterval.type == V4L2_FRMIVAL_TYPE_DISCRETE ? getIntervalFramerate(frameInterval.discrete) : VIDEO_FRAMERATE;
                CameraFormat candidateFormat = {true, caps, name, (int)frameSize.discrete.width, (int)frameSize.discrete.height, framerate, abs((int)frameSize.discrete.width - VIDEO_WIDTH), abs((int)frameSize.discrete.height - VIDEO_HEIGHT), abs(framerate - VIDEO_FRAMERATE)};
                if (isBetterCameraFormat(candidateFormat, bestFormat)) bestFormat = candidateFormat;
                frameInterval.index++;
            }
        }
        frameSize.index++;
    }
}

// Compare camera formats
bool isBetterCameraFormat(CameraFormat candidateFormat, CameraFormat bestFormat) {
    // Prefer the closest requested framerate
    if (!bestFormat.available) return true;
    if (candidateFormat.framerateDistance != bestFormat.framerateDistance) return candidateFormat.framerateDistance < bestFormat.framerateDistance;
    if (candidateFormat.widthDistance != bestFormat.widthDistance) return candidateFormat.widthDistance < bestFormat.widthDistance;
    if (candidateFormat.heightDistance != bestFormat.heightDistance) return candidateFormat.heightDistance < bestFormat.heightDistance;
    if (candidateFormat.caps != bestFormat.caps) return candidateFormat.caps == "image/jpeg";
    return false;
}

// Convert frame interval to framerate
int getIntervalFramerate(v4l2_fract interval) {
    // Round frames per second
    if (!interval.numerator) return VIDEO_FRAMERATE;
    return (interval.denominator + interval.numerator / 2) / interval.numerator;
}

// Get GStreamer caps for pixel format
string getCameraPixelCaps(__u32 pixelFormat) {
    // Map supported formats
    if (pixelFormat == V4L2_PIX_FMT_MJPEG) return "image/jpeg";
    if (pixelFormat == V4L2_PIX_FMT_YUYV) return "video/x-raw,format=YUY2";
    return "";
}

// Get readable pixel format name
string getCameraPixelName(__u32 pixelFormat) {
    // Map supported formats
    if (pixelFormat == V4L2_PIX_FMT_MJPEG) return "MJPG";
    if (pixelFormat == V4L2_PIX_FMT_YUYV) return "YUYV";
    return "unknown";
}

#endif

// Stop video pipeline
void cancelIceDisconnectTimer() {
    if (iceDisconnectTimerId) {
        g_source_remove(iceDisconnectTimerId);
        iceDisconnectTimerId = 0;
    }
}

void stopVideoPipeline() {
    // Ignore hangups from the webrtcbin we are about to drop
    videoSessionId++;
    cancelIceDisconnectTimer();

    // Stop watching remote frames, the window goes away with the pipeline
    if (remoteVideoTimerId) {
        g_source_remove(remoteVideoTimerId);
        remoteVideoTimerId = 0;
    }
    remoteVideoShowing = false;
    stopCallAudio();
    if (videoPipeline) gst_element_set_state(videoPipeline, GST_STATE_NULL);
    if (videoWebrtc) gst_object_unref(videoWebrtc);
    if (videoPipeline) gst_object_unref(videoPipeline);
    videoWebrtc = NULL;
    videoPipeline = NULL;
    remoteVideoSink = NULL;
    remoteVideoWidth = 0;
    remoteVideoHeight = 0;
    remoteDescriptionSet = false;

#ifdef HAVE_X11_FULLSCREEN
    // Close the fullscreen remote video window
    destroyFullscreenVideoWindow();
#endif
}

// Handle video offer
void handleVideoOffer(string payload) {
    // Decode offer
    JsonObject* object = decodeJson(payload);
    const char* type = json_object_get_string_member(object, "type");
    const char* sdpText = json_object_get_string_member(object, "sdp");
    if (!type || !sdpText || !g_str_equal(type, "offer")) {
        cout << "Ignoring non-offer video description." << endl;
        json_object_unref(object);
        return;
    }

    // Match the browser payload types and H264 profile for web viewers
    chooseVideoCodec(sdpText);
    chooseAudioCodec(sdpText);
    cout << "VIDEO_OFFER " << summarizeSdp(sdpText) << " h264_pt=" << videoPayloadType << " profile=" << videoProfileLevelId << " opus_pt=" << getCallAudioPayloadType() << endl;

    // Skip if camera is unavailable
    if (!videoCameraAvailable) {
        cout << "VIDEO_OFFER ignored, camera unavailable." << endl;
        json_object_unref(object);
        return;
    }

    // Keep the live webrtcbin, a second offer from Unmute adds audio
    if (videoWebrtc) {
        if (remoteDescriptionSet) cout << "Applying offer to the live WebRTC session." << endl;
        applyRemoteVideoOffer(sdpText);
        json_object_unref(object);
        return;
    }

    // Start send-only video, then answer this offer
    if (!videoPipeline) startVideoPipeline();
    if (!videoWebrtc) {
        cout << "VIDEO_OFFER ignored, no webrtcbin." << endl;
        json_object_unref(object);
        return;
    }
    waitForSenderCaps();
    prepareSenderTransceivers();
    applyRemoteVideoOffer(sdpText);
    json_object_unref(object);
}

// Handle video answer
void handleVideoAnswer(string payload) {
    // Decode answer
    JsonObject* object = decodeJson(payload);
    const char* type = json_object_get_string_member(object, "type");
    const char* sdpText = json_object_get_string_member(object, "sdp");
    if (!type || !sdpText || !g_str_equal(type, "answer")) {
        cout << "Ignoring non-answer video description." << endl;
        json_object_unref(object);
        return;
    }

    cout << "VIDEO_ANSWER " << summarizeSdp(sdpText) << endl;

    // Need an active caller pipeline
    if (!videoWebrtc) {
        cout << "VIDEO_ANSWER ignored, no webrtcbin." << endl;
        json_object_unref(object);
        return;
    }

    // Parse SDP
    GstSDPMessage* sdp = NULL;
    gst_sdp_message_new(&sdp);
    gst_sdp_message_parse_buffer((guint8*)sdpText, strlen(sdpText), sdp);

    // Set remote answer
    GstWebRTCSessionDescription* answer = gst_webrtc_session_description_new(GST_WEBRTC_SDP_TYPE_ANSWER, sdp);
    GstPromise* promise = gst_promise_new_with_change_func(onVideoAnswerSet, NULL, NULL);
    g_signal_emit_by_name(videoWebrtc, "set-remote-description", answer, promise);
    gst_webrtc_session_description_free(answer);
    json_object_unref(object);
}

// Create local offer as call caller
void createVideoOffer() {
    // Need an active pipeline
    if (!videoWebrtc) return;

    // Create offer
    GstPromise* promise = gst_promise_new_with_change_func(onVideoOfferCreated, NULL, NULL);
    g_signal_emit_by_name(videoWebrtc, "create-offer", NULL, promise);
}

// Pick the H264 payload type and profile the browser offered, webrtcbin matches profiles exactly
void chooseVideoCodec(const char* sdpText) {
    string sdp = sdpText ? sdpText : "";
    string marker = "a=rtpmap:";
    string codec = " H264/90000";

    // Try every H264 payload type the browser listed, in its order of preference
    int fallbackPayloadType = 0;
    string fallbackProfile;
    size_t search = 0;
    while (true) {
        size_t codecPosition = sdp.find(codec, search);
        if (codecPosition == string::npos) break;
        search = codecPosition + codec.size();

        // Read the payload type off the front of the rtpmap line
        size_t lineStart = sdp.rfind(marker, codecPosition);
        if (lineStart == string::npos) continue;
        lineStart += marker.size();
        int payloadType = atoi(sdp.substr(lineStart, codecPosition - lineStart).c_str());

        // Read the format line belonging to that payload type
        string formatMarker = "a=fmtp:" + to_string(payloadType) + " ";
        size_t formatStart = sdp.find(formatMarker);
        if (formatStart == string::npos) continue;
        formatStart += formatMarker.size();
        size_t formatEnd = sdp.find('\n', formatStart);
        string format = sdp.substr(formatStart, formatEnd == string::npos ? string::npos : formatEnd - formatStart);

        // Our payloader only sends whole access units, so packetization mode 1
        if (format.find("packetization-mode=1") == string::npos) continue;

        // Take baseline profiles only, x264enc sends constrained baseline
        string profileMarker = "profile-level-id=";
        size_t profileStart = format.find(profileMarker);
        if (profileStart == string::npos) continue;
        profileStart += profileMarker.size();
        size_t profileEnd = format.find_first_not_of("0123456789abcdefABCDEF", profileStart);
        string profile = format.substr(profileStart, profileEnd == string::npos ? string::npos : profileEnd - profileStart);
        if (profile.compare(0, 2, "42") != 0) continue;

        // Prefer constrained baseline, x264enc says constrained-baseline and profiles must agree
        if (profile == CONSTRAINED_BASELINE_PROFILE) {
            videoPayloadType = payloadType;
            videoProfileLevelId = profile;
            return;
        }
        if (!fallbackPayloadType) {
            fallbackPayloadType = payloadType;
            fallbackProfile = profile;
        }
    }

    // Settle for any baseline profile the browser listed
    if (fallbackPayloadType) {
        videoPayloadType = fallbackPayloadType;
        videoProfileLevelId = fallbackProfile;
    }
}

// Send Opus at the payload type the remote peer offered
void chooseAudioCodec(const char* sdpText) {
    int payloadType = findPayloadType(sdpText ? sdpText : "", " opus/48000");
    if (payloadType) setCallAudioPayloadType(payloadType);
}

// Read the payload type off the rtpmap line for a codec, a browser and a peer robot differ in case
int findPayloadType(string sdp, string codec) {
    string marker = "a=rtpmap:";
    for (char& letter : sdp) letter = tolower(letter);

    // The payload type sits at the front of the same line as the codec
    size_t codecPosition = sdp.find(codec);
    if (codecPosition == string::npos) return 0;
    size_t lineStart = sdp.rfind(marker, codecPosition);
    if (lineStart == string::npos) return 0;
    lineStart += marker.size();
    return atoi(sdp.substr(lineStart, codecPosition - lineStart).c_str());
}

// Name the codec and direction of every media line in an SDP
string summarizeSdp(const char* sdpText) {
    string text = sdpText ? sdpText : "";
    string summary;

    // Walk each media section, they start at an m= line and run to the next one
    size_t start = text.compare(0, 2, "m=") == 0 ? 0 : text.find("\nm=");
    while (start != string::npos) {
        if (text[start] == '\n') start++;
        size_t next = text.find("\nm=", start);
        string section = text.substr(start, next == string::npos ? string::npos : next - start);

        // Read the kind from the media line
        string kind = "media";
        if (section.compare(0, 7, "m=video") == 0) kind = "video";
        else if (section.compare(0, 7, "m=audio") == 0) kind = "audio";

        // Read the direction, an unnegotiated line is inactive
        string direction = "none";
        if (section.find("a=inactive") != string::npos) direction = "inactive";
        else if (section.find("a=sendrecv") != string::npos) direction = "sendrecv";
        else if (section.find("a=sendonly") != string::npos) direction = "sendonly";
        else if (section.find("a=recvonly") != string::npos) direction = "recvonly";

        // Read the codecs still on offer in this line, GStreamer and Chrome differ in case
        string upperSection = section;
        for (char& letter : upperSection) letter = toupper(letter);
        string codecs;
        const char* names[] = {"H264", "VP8", "VP9", "AV1", "OPUS", "PCMU"};
        for (const char* name : names) {
            if (upperSection.find(name) == string::npos) continue;
            if (!codecs.empty()) codecs += "/";
            codecs += name;
        }
        if (codecs.empty()) codecs = "no codec";

        if (!summary.empty()) summary += " ";
        summary += kind + "=" + direction + " " + codecs;
        start = next;
    }
    return summary;
}

// Hold the answer until the camera and mic have caps, webrtcbin drops a sender without them
void waitForSenderCaps() {
    if (!videoWebrtc) return;

    // The camera is sink_0, the mic is sink_1 when audio is in the pipeline
    const char* padNames[] = {"sink_0", "sink_1"};
    for (const char* padName : padNames) {
        GstPad* sinkPad = gst_element_get_static_pad(videoWebrtc, padName);
        if (!sinkPad) continue;

        // Poll for caps, a cold libcamera or a mic behind the canceller takes a moment
        int waited = 0;
        GstCaps* caps = NULL;
        while (waited < SENDER_CAPS_WAIT_MS) {
            caps = gst_pad_get_current_caps(sinkPad);
            if (caps) break;
            g_usleep(SENDER_CAPS_POLL_MS * 1000);
            waited += SENDER_CAPS_POLL_MS;
        }

        // Log that the sender is ready, or that it never produced anything
        string sender = g_str_equal(padName, "sink_0") ? "Camera" : "Mic";
        if (caps) {
            cout << sender << " ready after " << waited << "ms." << endl;
            gst_caps_unref(caps);
        }
        else {
            logGStreamerError(sender + " produced nothing in " + to_string(SENDER_CAPS_WAIT_MS) + "ms, answering without it.");
        }
        gst_object_unref(sinkPad);
    }
}

// Pin each sender, webrtcbin otherwise answers with a codec we cannot produce
void prepareSenderTransceivers() {
    prepareSenderTransceiver(0, "video", "H264", videoPayloadType, GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV);
    if (includeAudioInPipeline) prepareSenderTransceiver(1, "audio", "OPUS", getCallAudioPayloadType(), GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDRECV);
}

// Set one transceiver to a single codec and direction
void prepareSenderTransceiver(int index, string media, string codec, int payloadType, GstWebRTCRTPTransceiverDirection direction) {
    if (!videoWebrtc) return;

    // Find the transceiver webrtcbin made for this sink pad
    GstWebRTCRTPTransceiver* transceiver = NULL;
    g_signal_emit_by_name(videoWebrtc, "get-transceiver", index, &transceiver);
    if (!transceiver) {
        cout << "No " << media << " transceiver to prepare." << endl;
        return;
    }

    // Offer only our codec, at the payload type the remote peer asked for
    int clockRate = media == "video" ? VIDEO_CLOCK_RATE : AUDIO_CLOCK_RATE;
    string capsText = "application/x-rtp,media=" + media + ",encoding-name=" + codec + ",clock-rate=" + to_string(clockRate) + ",payload=" + to_string(payloadType);
    GstCaps* caps = gst_caps_from_string(capsText.c_str());
    g_object_set(transceiver, "codec-preferences", caps, NULL);
    g_object_set(transceiver, "direction", direction, NULL);
    gst_caps_unref(caps);

    cout << (media == "video" ? "Video" : "Audio") << " transceiver " << codec << " pt=" << payloadType << " " << (direction == GST_WEBRTC_RTP_TRANSCEIVER_DIRECTION_SENDONLY ? "sendonly" : "sendrecv") << "." << endl;
    gst_object_unref(transceiver);
}

// Set the browser or peer offer on webrtcbin
void applyRemoteVideoOffer(const char* sdpText) {
    GstSDPMessage* sdp = NULL;
    gst_sdp_message_new(&sdp);
    gst_sdp_message_parse_buffer((guint8*)sdpText, strlen(sdpText), sdp);
    GstWebRTCSessionDescription* offer = gst_webrtc_session_description_new(GST_WEBRTC_SDP_TYPE_OFFER, sdp);
    GstPromise* promise = gst_promise_new_with_change_func(onVideoOfferSet, NULL, NULL);
    g_signal_emit_by_name(videoWebrtc, "set-remote-description", offer, promise);
    gst_webrtc_session_description_free(offer);
}

// Handle video address
void handleVideoAddress(string payload) {
    // Decode candidate
    JsonObject* object = decodeJson(payload);
    const char* candidate = json_object_get_string_member(object, "candidate");
    int mediaLineIndex = json_object_get_int_member(object, "sdpMLineIndex");

    if (!candidate || !candidate[0]) {
        json_object_unref(object);
        return;
    }

    // Hold addresses until the remote description is on this webrtcbin
    if (!videoWebrtc || !remoteDescriptionSet) {
        pendingVideoAddresses.push_back(payload);
        json_object_unref(object);
        return;
    }

    g_signal_emit_by_name(videoWebrtc, "add-ice-candidate", mediaLineIndex, candidate);
    json_object_unref(object);
}

// Add addresses that arrived before the remote description
void applyPendingVideoAddresses() {
    if (!videoWebrtc || !remoteDescriptionSet) return;
    vector<string> addresses = pendingVideoAddresses;
    pendingVideoAddresses.clear();
    for (const string& address : addresses) handleVideoAddress(address);
}

// Send video description
void sendVideoDescription(GstWebRTCSessionDescription* description) {
    // Build description
    gchar* sdpText = gst_sdp_message_as_text(description->sdp);
    const char* typeName = description->type == GST_WEBRTC_SDP_TYPE_OFFER ? "offer" : "answer";
    const char* command = description->type == GST_WEBRTC_SDP_TYPE_OFFER ? "VIDEO_OFFER" : "VIDEO_ANSWER";
    JsonObject* object = json_object_new();
    json_object_set_string_member(object, "type", typeName);
    json_object_set_string_member(object, "sdp", sdpText);

    // Send offer or answer
    string payload = encodeJson(object);
    if (videoWebSocket) videoWebSocket->send(videoDeviceName + " " + command + " " + payload);
    cout << "Sent " << command << " " << summarizeSdp(sdpText) << endl;
    json_object_unref(object);
    g_free(sdpText);
}

// Handle remote WebRTC stream pad
void onIncomingStream(GstElement* element, GstPad* pad, gpointer userData) {
    // Ignore unused values
    (void)element;
    (void)userData;

    // Only link source pads once
    if (GST_PAD_DIRECTION(pad) != GST_PAD_SRC) return;
    if (gst_pad_is_linked(pad)) return;

    // Log the incoming RTP pad
    GstCaps* padCaps = gst_pad_get_current_caps(pad);
    if (!padCaps) padCaps = gst_pad_query_caps(pad, NULL);
    gchar* capsText = padCaps ? gst_caps_to_string(padCaps) : g_strdup("none");
    cout << "Remote WebRTC pad " << (GST_PAD_NAME(pad) ? GST_PAD_NAME(pad) : "?") << " caps=" << capsText << endl;
    g_free(capsText);
    if (padCaps) gst_caps_unref(padCaps);

    // Decode remote RTP with decodebin
    GstElement* decodebin = gst_element_factory_make("decodebin", NULL);
    if (!decodebin) {
        cout << "Failed to create decodebin for remote stream." << endl;
        return;
    }
    gst_bin_add(GST_BIN(videoPipeline), decodebin);
    g_signal_connect(decodebin, "pad-added", G_CALLBACK(onDecodedStream), NULL);
    gst_element_sync_state_with_parent(decodebin);

    // Link webrtcbin pad into decodebin
    GstPad* sinkPad = gst_element_get_static_pad(decodebin, "sink");
    GstPadLinkReturn linkResult = gst_pad_link(pad, sinkPad);
    gst_object_unref(sinkPad);
    if (linkResult != GST_PAD_LINK_OK) cout << "Failed to link remote WebRTC pad." << endl;
}

// Handle decoded remote audio or video
void onDecodedStream(GstElement* decodebin, GstPad* pad, gpointer userData) {
    // Ignore unused values
    (void)decodebin;
    (void)userData;

    // Read media type from caps
    GstCaps* caps = gst_pad_get_current_caps(pad);
    if (!caps) caps = gst_pad_query_caps(pad, NULL);
    if (!caps) {
        cout << "Remote decoded pad has no caps." << endl;
        return;
    }
    const GstStructure* structure = gst_caps_get_structure(caps, 0);
    const gchar* mediaName = gst_structure_get_name(structure);
    bool isVideo = g_str_has_prefix(mediaName, "video/");
    bool isAudio = g_str_has_prefix(mediaName, "audio/");
    cout << "Decoded remote stream " << (mediaName ? mediaName : "?") << endl;
    gst_caps_unref(caps);
    if (!isVideo && !isAudio) return;

    // Play remote video fullscreen, from a peer robot or from the web page camera
    if (isVideo) {
        GstElement* queue = gst_element_factory_make("queue", NULL);
        GstElement* convert = NULL;
        GstElement* sink = NULL;

        // Prefer Jetson EGL sink so aspect ratio is kept, nv3dsink stretches to fill
        if (isGStreamerElementAvailable("nveglglessink") && isGStreamerElementAvailable("nvvidconv")) {
            convert = gst_element_factory_make("nvvidconv", NULL);
            sink = gst_element_factory_make("nveglglessink", "remotesink");
            cout << "Using nveglglessink for remote video." << endl;
        } else if (isGStreamerElementAvailable("nv3dsink") && isGStreamerElementAvailable("nvvidconv")) {
            convert = gst_element_factory_make("nvvidconv", NULL);
            sink = gst_element_factory_make("nv3dsink", "remotesink");
            cout << "Using nv3dsink for remote video." << endl;
        } else {
            convert = gst_element_factory_make("videoconvert", NULL);
            sink = gst_element_factory_make("xvimagesink", "remotesink");
            if (!sink) sink = gst_element_factory_make("ximagesink", "remotesink");
            if (!sink) sink = gst_element_factory_make("autovideosink", "remotesink");
        }
        if (!queue || !convert || !sink) {
            cout << "Failed to create remote video sink." << endl;
            return;
        }
        g_object_set(queue, "max-size-buffers", 2, "max-size-time", 0, "max-size-bytes", 0, "leaky", 2, NULL);
        g_object_set(sink, "sync", FALSE, NULL);
        if (g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "force-aspect-ratio")) {
            g_object_set(sink, "force-aspect-ratio", TRUE, NULL);
        }
        if (g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "create-window")) {
            g_object_set(sink, "create-window", FALSE, NULL);
        }
        remoteVideoSink = sink;
        GstPad* overlayPad = gst_element_get_static_pad(sink, "sink");
        if (overlayPad) {
            gst_pad_add_probe(overlayPad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM, onRemoteVideoCaps, NULL, NULL);
            gst_object_unref(overlayPad);
        }
        gst_bin_add_many(GST_BIN(videoPipeline), queue, convert, sink, NULL);
        gst_element_sync_state_with_parent(queue);
        gst_element_sync_state_with_parent(convert);
        gst_element_sync_state_with_parent(sink);
        gst_element_link_many(queue, convert, sink, NULL);
        GstPad* sinkPad = gst_element_get_static_pad(queue, "sink");
        gst_pad_link(pad, sinkPad);
        gst_object_unref(sinkPad);
        cout << "Linked remote video stream." << endl;

        // Watch the frames, a peer turning its camera off just stops sending
        remoteVideoBufferTimeUs = g_get_monotonic_time();
        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, noteRemoteVideoBuffer, NULL, NULL);
        startRemoteVideoIdleTimer();
        return;
    }

    linkRemoteAudioPad(videoPipeline, pad);
}

// True when the sink letterboxes instead of stretching
bool remoteSinkKeepsAspect(GstElement* sink) {
    if (!sink) return false;
    return g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "force-aspect-ratio") != NULL;
}

// Fit remote video into the fullscreen window without stretching
void applyRemoteVideoLetterbox() {
#ifdef HAVE_X11_FULLSCREEN
    if (!remoteVideoSink || !GST_IS_VIDEO_OVERLAY(remoteVideoSink)) return;
    if (remoteSinkKeepsAspect(remoteVideoSink)) return;
    if (!videoDisplay || remoteVideoWidth < 1 || remoteVideoHeight < 1) return;
    int screen = DefaultScreen(videoDisplay);
    int windowWidth = DisplayWidth(videoDisplay, screen);
    int windowHeight = DisplayHeight(videoDisplay, screen);
    if (windowWidth < 1 || windowHeight < 1) return;
    int scaledWidth = windowWidth;
    int scaledHeight = remoteVideoHeight * windowWidth / remoteVideoWidth;
    if (scaledHeight > windowHeight) {
        scaledHeight = windowHeight;
        scaledWidth = remoteVideoWidth * windowHeight / remoteVideoHeight;
    }
    scaledWidth &= ~1;
    scaledHeight &= ~1;
    if (scaledWidth < 2) scaledWidth = 2;
    if (scaledHeight < 2) scaledHeight = 2;
    int x = (windowWidth - scaledWidth) / 2;
    int y = (windowHeight - scaledHeight) / 2;
    gst_video_overlay_set_render_rectangle(GST_VIDEO_OVERLAY(remoteVideoSink), x, y, scaledWidth, scaledHeight);
    cout << "Remote video letterbox " << scaledWidth << "x" << scaledHeight << " in " << windowWidth << "x" << windowHeight << endl;
#endif
}

// Read decoded frame size so Jetson can letterbox
GstPadProbeReturn onRemoteVideoCaps(GstPad* pad, GstPadProbeInfo* info, gpointer userData) {
    (void)pad;
    (void)userData;
    if ((GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM) == 0) return GST_PAD_PROBE_OK;
    GstEvent* event = gst_pad_probe_info_get_event(info);
    if (!event || GST_EVENT_TYPE(event) != GST_EVENT_CAPS) return GST_PAD_PROBE_OK;
    GstCaps* caps = NULL;
    gst_event_parse_caps(event, &caps);
    if (!caps) return GST_PAD_PROBE_OK;
    GstVideoInfo videoInfo;
    if (!gst_video_info_from_caps(&videoInfo, caps)) return GST_PAD_PROBE_OK;
    remoteVideoWidth = GST_VIDEO_INFO_WIDTH(&videoInfo);
    remoteVideoHeight = GST_VIDEO_INFO_HEIGHT(&videoInfo);
    applyRemoteVideoLetterbox();
    return GST_PAD_PROBE_OK;
}

// Send video address
void sendVideoAddress(GstElement* element, guint mediaLineIndex, gchar* candidate, gpointer userData) {
    // Ignore unused values
    (void)element;
    (void)userData;

    // Build candidate
    JsonObject* object = json_object_new();
    json_object_set_string_member(object, "candidate", candidate);
    json_object_set_int_member(object, "sdpMLineIndex", mediaLineIndex);
    string mid = to_string(mediaLineIndex);
    json_object_set_string_member(object, "sdpMid", mid.c_str());

    // Send candidate
    string payload = encodeJson(object);
    if (videoWebSocket) videoWebSocket->send(videoDeviceName + " VIDEO_ADDRESS " + payload);
    json_object_unref(object);
}

#ifdef HAVE_X11_FULLSCREEN
// Create or reuse a fullscreen X11 window for remote video
Window ensureFullscreenVideoWindow() {
    // Open the local display
    if (!videoDisplay) videoDisplay = XOpenDisplay(NULL);
    if (!videoDisplay) {
        cout << "Failed to open X11 display for fullscreen video." << endl;
        return 0;
    }
    if (videoWindow) return videoWindow;

    // Create a black window covering the screen
    int screen = DefaultScreen(videoDisplay);
    int width = DisplayWidth(videoDisplay, screen);
    int height = DisplayHeight(videoDisplay, screen);
    videoWindow = XCreateSimpleWindow(videoDisplay, RootWindow(videoDisplay, screen), 0, 0, width, height, 0, BlackPixel(videoDisplay, screen), BlackPixel(videoDisplay, screen));
    XStoreName(videoDisplay, videoWindow, "Teleport");

    // Ask the window manager for fullscreen
    Atom wmState = XInternAtom(videoDisplay, "_NET_WM_STATE", False);
    Atom wmFullscreen = XInternAtom(videoDisplay, "_NET_WM_STATE_FULLSCREEN", False);
    XChangeProperty(videoDisplay, videoWindow, wmState, XA_ATOM, 32, PropModeReplace, (unsigned char*)&wmFullscreen, 1);

    // Show the window
    XMapRaised(videoDisplay, videoWindow);
    XSync(videoDisplay, False);
    remoteVideoShowing = true;
    cout << "Remote video fullscreen " << width << "x" << height << endl;
    return videoWindow;
}

// Ask the window manager for fullscreen, the property alone only counts before the first map
void requestFullscreenVideoWindow() {
    if (!videoDisplay || !videoWindow) return;
    XEvent event;
    memset(&event, 0, sizeof(event));
    event.type = ClientMessage;
    event.xclient.window = videoWindow;
    event.xclient.message_type = XInternAtom(videoDisplay, "_NET_WM_STATE", False);
    event.xclient.format = 32;
    event.xclient.data.l[0] = NET_WM_STATE_ADD;
    event.xclient.data.l[1] = XInternAtom(videoDisplay, "_NET_WM_STATE_FULLSCREEN", False);
    event.xclient.data.l[3] = 1;
    XSendEvent(videoDisplay, DefaultRootWindow(videoDisplay), False, SubstructureRedirectMask | SubstructureNotifyMask, &event);
    XSync(videoDisplay, False);
}

// Destroy the fullscreen remote video window
void destroyFullscreenVideoWindow() {
    if (!videoDisplay || !videoWindow) return;
    XDestroyWindow(videoDisplay, videoWindow);
    XSync(videoDisplay, False);
    videoWindow = 0;
}
#endif

// Poll for remote frames stopping, a peer camera going off sends no packets and no event
void startRemoteVideoIdleTimer() {
    if (remoteVideoTimerId || !videoLoop) return;
    GSource* source = g_timeout_source_new(REMOTE_VIDEO_POLL_MS);
    g_source_set_callback(source, hideRemoteVideoWhenIdle, NULL, NULL);
    remoteVideoTimerId = g_source_attach(source, g_main_loop_get_context(videoLoop));
    g_source_unref(source);
}

// Note the arrival time of each remote frame, and show the screen again on the first one
GstPadProbeReturn noteRemoteVideoBuffer(GstPad* pad, GstPadProbeInfo* info, gpointer userData) {
    // Ignore unused values
    (void)pad;
    (void)info;
    (void)userData;

    remoteVideoBufferTimeUs = g_get_monotonic_time();
    if (!remoteVideoShowing) showRemoteVideoWindow();
    return GST_PAD_PROBE_OK;
}

// Clear the screen once the remote frames have stopped for a moment
gboolean hideRemoteVideoWhenIdle(gpointer userData) {
    (void)userData;

    if (!remoteVideoShowing) return TRUE;
    gint64 idleMs = (g_get_monotonic_time() - remoteVideoBufferTimeUs) / 1000;
    if (idleMs < REMOTE_VIDEO_IDLE_MS) return TRUE;
    hideRemoteVideoWindow();
    return TRUE;
}

// Map the window again, and ask for fullscreen since mapping loses that state
void showRemoteVideoWindow() {
    remoteVideoShowing = true;
#ifdef HAVE_X11_FULLSCREEN
    if (videoDisplay && videoWindow) {
        XMapRaised(videoDisplay, videoWindow);
        requestFullscreenVideoWindow();
    }
#endif
    cout << "Remote video on screen." << endl;
}

// Take the window off the screen, a fullscreen window stays on top so lowering it does nothing
void hideRemoteVideoWindow() {
    remoteVideoShowing = false;
#ifdef HAVE_X11_FULLSCREEN
    if (videoDisplay && videoWindow) {
        XUnmapWindow(videoDisplay, videoWindow);
        XSync(videoDisplay, False);
    }
#endif
    cout << "Remote video stopped, screen cleared." << endl;
}

// Log once when camera frames reach webrtcbin
GstPadProbeReturn logFirstCameraBuffer(GstPad* pad, GstPadProbeInfo* info, gpointer userData) {
    // Confirm camera packets reach webrtcbin
    (void)pad;
    (void)info;
    (void)userData;
    cout << "Camera sending to WebRTC." << endl;
    return GST_PAD_PROBE_REMOVE;
}

// Catch prepare-window-handle so remote video renders into our fullscreen window
GstBusSyncReply handleVideoBusSync(GstBus* bus, GstMessage* message, gpointer userData) {
    (void)bus;
    (void)userData;

#ifdef HAVE_X11_FULLSCREEN
    // Bind the video sink to the fullscreen X11 window
    if (gst_is_video_overlay_prepare_window_handle_message(message)) {
        Window window = ensureFullscreenVideoWindow();
        if (window) {
            remoteVideoSink = GST_ELEMENT(GST_MESSAGE_SRC(message));
            gst_video_overlay_set_window_handle(GST_VIDEO_OVERLAY(GST_MESSAGE_SRC(message)), (guintptr)window);
            if (remoteSinkKeepsAspect(remoteVideoSink)) {
                gst_video_overlay_set_render_rectangle(GST_VIDEO_OVERLAY(remoteVideoSink), 0, 0, -1, -1);
            } else {
                applyRemoteVideoLetterbox();
            }
        }
        gst_message_unref(message);
        return GST_BUS_DROP;
    }
#endif

    return GST_BUS_PASS;
}

// Print a GStreamer error at journal priority 3 so journalctl shows it red
void logGStreamerError(const string& message) {
    if (!isatty(STDERR_FILENO)) {
        cerr << "<3>" << message << endl;
        return;
    }
    cerr << "\033[1;31m" << message << "\033[0m" << endl;
}

// Handle video bus message
gboolean handleVideoBusMessage(GstBus* bus, GstMessage* message, gpointer userData) {
    // Ignore unused values
    (void)bus;
    (void)userData;

    // Element messages need no handling
    if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ELEMENT) return TRUE;

    // Log errors
    if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
        GError* error = NULL;
        gchar* debug = NULL;
        gst_message_parse_error(message, &error, &debug);
        string errorMessage = error ? error->message : "";
        string debugMessage = debug ? debug : "";

        // USB sound card unplugged or bumped loose, log once and skip camera dump
        if (isCallAudioDisconnectMessage(errorMessage, debugMessage)) {
            if (shouldLogCallAudioDisconnect()) logGStreamerError("Audio device disconnected.");
        } else if (errorMessage.find("Cannot identify device") != string::npos || debugMessage.find("No such file or directory") != string::npos) {
            logGStreamerError("No camera connected.");
            printCameraFormats();
        } else {
            logGStreamerError("GStreamer error: " + errorMessage);
            if (debug) logGStreamerError(string("GStreamer debug: ") + debug);
        }
        g_clear_error(&error);
        g_free(debug);
    }

    // Log warnings, skip expected mic backlog and audio-disconnect repeats
    else if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_WARNING) {
        GError* error = NULL;
        gchar* debug = NULL;
        gst_message_parse_warning(message, &error, &debug);
        string warningMessage = error ? error->message : "";
        string debugMessage = debug ? debug : "";
        bool skipWarning = warningMessage.find("Can't record audio fast enough") != string::npos ||
            isCallAudioDisconnectMessage(warningMessage, debugMessage);
        if (!skipWarning) {
            cout << "GStreamer warning: " << warningMessage << endl;
            if (debug) cout << "GStreamer debug: " << debug << endl;
        }
        g_clear_error(&error);
        g_free(debug);
    }

    // Keep watch active
    return TRUE;
}

// Stop video from the GStreamer main loop after the peer drops
gboolean stopVideoFromMainLoop(gpointer userData) {
    int sessionId = GPOINTER_TO_INT(userData);
    if (sessionId != videoSessionId) {
        cout << "Ignoring disconnect from old WebRTC session " << sessionId << "." << endl;
        return G_SOURCE_REMOVE;
    }
    if (!videoRunning) return G_SOURCE_REMOVE;
    cout << "Remote peer disconnected, closing video." << endl;
    stopVideo();
    return G_SOURCE_REMOVE;
}

// Log connection state
void logVideoConnectionState(GObject* object, GParamSpec* spec, gpointer userData) {
    // Ignore unused values
    (void)spec;
    (void)userData;

    // Read state
    GstWebRTCPeerConnectionState state;
    g_object_get(object, "connection-state", &state, NULL);
    cout << "WebRTC connection " << videoConnectionStateName(state) << endl;
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_FAILED ||
        state == GST_WEBRTC_PEER_CONNECTION_STATE_CLOSED) {
        g_idle_add(stopVideoFromMainLoop, GINT_TO_POINTER(videoSessionId));
    }
}

// Log ICE connection state
gboolean iceDisconnectHangup(gpointer userData) {
    (void)userData;
    iceDisconnectTimerId = 0;
    if (iceDisconnectSessionId != videoSessionId) {
        cout << "Ignoring hangup from old WebRTC session." << endl;
        return G_SOURCE_REMOVE;
    }
    if (!videoRunning) return G_SOURCE_REMOVE;
    cout << "WebRTC connection disconnected, closing video." << endl;
    stopVideo();
    return G_SOURCE_REMOVE;
}

void logVideoIceConnectionState(GObject* object, GParamSpec* spec, gpointer userData) {
    (void)spec;
    (void)userData;

    GstWebRTCICEConnectionState state;
    g_object_get(object, "ice-connection-state", &state, NULL);
    cout << "WebRTC connection " << videoConnectionStateName(state) << endl;

    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_CONNECTED ||
        state == GST_WEBRTC_ICE_CONNECTION_STATE_COMPLETED) {
        cancelIceDisconnectTimer();
        return;
    }

    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_FAILED ||
        state == GST_WEBRTC_ICE_CONNECTION_STATE_CLOSED) {
        g_idle_add(stopVideoFromMainLoop, GINT_TO_POINTER(videoSessionId));
        return;
    }

    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_DISCONNECTED && videoRunning && videoLoop && !iceDisconnectTimerId) {
        iceDisconnectSessionId = videoSessionId;
        GSource* source = g_timeout_source_new(ICE_DISCONNECT_HANGOVER_MS);
        g_source_set_callback(source, iceDisconnectHangup, NULL, NULL);
        iceDisconnectTimerId = g_source_attach(source, g_main_loop_get_context(videoLoop));
        g_source_unref(source);
    }
}

// Log signaling state
void logVideoSignalingState(GObject* object, GParamSpec* spec, gpointer userData) {
    // Ignore unused values
    (void)spec;
    (void)userData;

    // Read state
    GstWebRTCSignalingState state;
    g_object_get(object, "signaling-state", &state, NULL);
    cout << "WebRTC signaling " << videoSignalingStateName(state) << endl;
}

// Name peer connection state
const char* videoConnectionStateName(GstWebRTCPeerConnectionState state) {
    // Map state
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_NEW) return "new";
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_CONNECTING) return "connecting";
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_CONNECTED) return "connected";
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_DISCONNECTED) return "disconnected";
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_FAILED) return "failed";
    if (state == GST_WEBRTC_PEER_CONNECTION_STATE_CLOSED) return "closed";
    return "unknown";
}

// Name transport connection state
const char* videoConnectionStateName(GstWebRTCICEConnectionState state) {
    // Map state
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_NEW) return "new";
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_CHECKING) return "checking";
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_CONNECTED) return "connected";
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_COMPLETED) return "completed";
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_FAILED) return "failed";
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_DISCONNECTED) return "disconnected";
    if (state == GST_WEBRTC_ICE_CONNECTION_STATE_CLOSED) return "closed";
    return "unknown";
}

// Name signaling state
const char* videoSignalingStateName(GstWebRTCSignalingState state) {
    // Map state
    if (state == GST_WEBRTC_SIGNALING_STATE_STABLE) return "stable";
    if (state == GST_WEBRTC_SIGNALING_STATE_HAVE_LOCAL_OFFER) return "have-local-offer";
    if (state == GST_WEBRTC_SIGNALING_STATE_HAVE_REMOTE_OFFER) return "have-remote-offer";
    if (state == GST_WEBRTC_SIGNALING_STATE_HAVE_LOCAL_PRANSWER) return "have-local-pranswer";
    if (state == GST_WEBRTC_SIGNALING_STATE_HAVE_REMOTE_PRANSWER) return "have-remote-pranswer";
    if (state == GST_WEBRTC_SIGNALING_STATE_CLOSED) return "closed";
    return "unknown";
}

// Handle offer set
void onVideoOfferSet(GstPromise* promise, gpointer userData) {
    // Ignore unused value
    (void)userData;

    // Log offer
    remoteDescriptionSet = true;
    applyPendingVideoAddresses();

    // Create answer
    gst_promise_unref(promise);
    promise = gst_promise_new_with_change_func(onVideoAnswerCreated, NULL, NULL);
    g_signal_emit_by_name(videoWebrtc, "create-answer", NULL, promise);
}

// Handle answer created
void onVideoAnswerCreated(GstPromise* promise, gpointer userData) {
    // Ignore unused value
    (void)userData;

    // Read answer
    GstWebRTCSessionDescription* answer = NULL;
    const GstStructure* reply = gst_promise_get_reply(promise);
    if (reply) gst_structure_get(reply, "answer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &answer, NULL);
    gst_promise_unref(promise);
    if (!answer) {
        cout << "Failed to create video answer." << endl;
        return;
    }

    // Set local answer
    promise = gst_promise_new();
    g_signal_emit_by_name(videoWebrtc, "set-local-description", answer, promise);
    gst_promise_interrupt(promise);
    gst_promise_unref(promise);

    sendVideoDescription(answer);
    gst_webrtc_session_description_free(answer);
}

// Handle offer created
void onVideoOfferCreated(GstPromise* promise, gpointer userData) {
    // Ignore unused value
    (void)userData;

    // Read offer
    GstWebRTCSessionDescription* offer = NULL;
    const GstStructure* reply = gst_promise_get_reply(promise);
    if (reply) gst_structure_get(reply, "offer", GST_TYPE_WEBRTC_SESSION_DESCRIPTION, &offer, NULL);
    gst_promise_unref(promise);
    if (!offer) {
        cout << "Failed to create video offer." << endl;
        return;
    }

    // Set local offer
    promise = gst_promise_new();
    g_signal_emit_by_name(videoWebrtc, "set-local-description", offer, promise);
    gst_promise_interrupt(promise);
    gst_promise_unref(promise);

    sendVideoDescription(offer);
    gst_webrtc_session_description_free(offer);
}

// Handle remote answer set
void onVideoAnswerSet(GstPromise* promise, gpointer userData) {
    // Ignore unused value
    (void)userData;

    // Log answer
    remoteDescriptionSet = true;
    applyPendingVideoAddresses();
    gst_promise_unref(promise);
}

// Encode JSON
string encodeJson(JsonObject* object) {
    // Serialize JSON
    JsonNode* root = json_node_init_object(json_node_alloc(), object);
    JsonGenerator* generator = json_generator_new();
    json_generator_set_root(generator, root);
    gchar* text = json_generator_to_data(generator, NULL);

    // Encode JSON
    gchar* encoded = g_base64_encode((guchar*)text, strlen(text));
    string payload = encoded;

    // Release JSON
    g_free(encoded);
    g_free(text);
    g_object_unref(generator);
    json_node_free(root);
    return payload;
}

// Decode JSON
JsonObject* decodeJson(string payload) {
    // Decode payload
    gsize length = 0;
    guchar* text = g_base64_decode(payload.c_str(), &length);

    // Parse JSON
    JsonParser* parser = json_parser_new();
    GError* error = NULL;
    json_parser_load_from_data(parser, (const gchar*)text, length, &error);
    if (error) {
        cout << "Failed to parse signaling JSON: " << error->message << endl;
        g_error_free(error);
        g_free(text);
        g_object_unref(parser);
        return json_object_new();
    }

    // Copy object
    JsonNode* root = json_parser_get_root(parser);
    JsonObject* object = json_object_ref(json_node_get_object(root));

    // Release parser
    g_free(text);
    g_object_unref(parser);
    return object;
}

// Print supported camera formats
void printCameraFormats() {
#ifdef __linux__
    // Run V4L2 format query
    string cameraDevice = getCameraDevice();
    string command = "v4l2-ctl --device=" + shellQuote(cameraDevice) + " --list-formats-ext 2>&1";
    FILE* pipe = popen(command.c_str(), "r");
    if (!pipe) {
        cout << "Unable to list camera formats with v4l2-ctl." << endl;
        return;
    }

    // Print query output
    cout << "Camera formats for " << cameraDevice << ":" << endl;
    char buffer[256];
    while (fgets(buffer, sizeof(buffer), pipe)) cout << buffer;
    pclose(pipe);
#else
    // Log unsupported platform
    cout << "Camera format listing is only supported on Linux." << endl;
#endif
}

// Quote shell argument
string shellQuote(string value) {
    // Build single quoted string
    string quoted = "'";
    for (char character : value) {
        if (character == '\'') quoted += "'\\''";
        else quoted += character;
    }
    quoted += "'";
    return quoted;
}

// Check file exists
bool videoFileExists(string path) {
    // Stat file
    struct stat status;
    return stat(path.c_str(), &status) == 0;
}

#endif

// Close the GStreamer WebRTC guard at the top of the file
#endif
