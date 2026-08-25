/*
 * Call mic, speaker, echo cancel.
*/

#include "audio.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <mutex>
#include <string>
#include <vector>

#ifdef HAVE_GSTREAMER_WEBRTC
#include <gst/app/gstappsrc.h>
#include <gst/fft/gstfftf32.h>
#endif

using namespace std;
using namespace std::chrono;

const double VIDEO_SPEAKER_VOLUME = 0.45;
const int VIDEO_AUDIO_BUFFER_TIME = 80000;
const int VIDEO_AUDIO_LATENCY_TIME = 20000;
const int VIDEO_AUDIO_RATE = 48000;
const int VIDEO_AUDIO_QUEUE_MS = 200;
const int CALL_MIC_GAIN_DB = 0;
const int CALL_MIC_LIMIT_DBFS = 3;
const int TONE_FFT_SIZE = 2048;
const int TONE_HOP = 1024;
const double TONE_MIN_HZ = 200.0;
const double TONE_MAX_HZ = 5000.0;
const double TONE_DOMINANCE_DB = 20.0;
const double TONE_MIN_PEAK_DB = -45.0;
const double TONE_HARMONIC_DB = 15.0;
const int TONE_STABLE_FRAMES = 15;
const int TONE_BIN_TOLERANCE = 1;
const int NOTCH_COUNT = 12;
const int NOTCH_POLES = 8;
const double NOTCH_RIPPLE_DB = 0.05;
const double NOTCH_HALF_WIDTH_HZ = 30.0;
const double NOTCH_WIDEN_HZ = 15.0;
const double NOTCH_MAX_HALF_WIDTH_HZ = 75.0;
const double NOTCH_PARK_LOW_HZ = 23500.0;
const double NOTCH_PARK_HIGH_HZ = 23900.0;
const double NOTCH_MATCH_HZ = 30.0;
const int NOTCH_SETTLE_MS = 700;
const int NOTCH_HOLD_MS = 60000;
const int NOTCH_POLL_MS = 500;

string videoAudioDevice = "plughw:0,0";
string videoMicDevice = "plughw:0,0";
string videoSpeakerDevice = "plughw:0,0";
bool videoMuteMic = false;

#ifdef HAVE_GSTREAMER_WEBRTC
bool audioDisconnectLogged = false;
GstElement* remotePcmPipeline = NULL;
GstElement* remotePcmSrc = NULL;
GstElement* micToneTapElement = NULL;
GstFFTF32* toneFft = NULL;
float toneWindow[TONE_FFT_SIZE];
int toneWindowFill = 0;
int toneStableFrames = 0;
int toneStableBin = 0;
GstElement* notchElements[NOTCH_COUNT];
double notchHertz[NOTCH_COUNT];
double notchHalfWidth[NOTCH_COUNT];
steady_clock::time_point notchSetAt[NOTCH_COUNT];
steady_clock::time_point notchSeenAt[NOTCH_COUNT];
guint notchTimerId = 0;
mutex notchLock;

void attachEchoCanceller(GstElement* pipeline);
void attachMicToneProbe(GstElement* element);
void appendToneSamples(GstPad* pad, GstBuffer* buffer);
bool detectTonePeak(double& hertz);
double spectrumBinDb(GstFFTF32Complex* spectrum, int bin);
void notchMicTone(double hertz);
void engageNotch(int index, double hertz, double halfWidth);
void parkNotch(int index);
int leastRecentNotch();
gboolean pollNotches(gpointer userData);
GstPadProbeReturn onMicToneProbe(GstPad* pad, GstPadProbeInfo* info, gpointer userData);
bool isGStreamerAudioElement(string name);
string echoCancelVersion();
bool isPulseAudioDevice(const string& device);
void quietGstAudioLog(const gchar* logDomain, GLogLevelFlags logLevel, const gchar* message, gpointer userData);
#endif

string cardStreamInfo(int card);
bool cardHasCapture(int card);
bool cardHasPlayback(int card);
vector<int> listUsbCards();
vector<int> listUsbCaptureCards();
vector<string> listPulseShortNames(const char* command);
bool isUsbPulseAudioName(const string& name);

// Read /proc/asound/cardN/stream0 text
string cardStreamInfo(int card) {
    ifstream streamFile("/proc/asound/card" + to_string(card) + "/stream0");
    if (!streamFile) return "";
    return string((istreambuf_iterator<char>(streamFile)), istreambuf_iterator<char>());
}

// True when an ALSA card has a USB capture stream
bool cardHasCapture(int card) {
    return cardStreamInfo(card).find("Capture:") != string::npos;
}

// True when an ALSA card has a USB playback stream
bool cardHasPlayback(int card) {
    return cardStreamInfo(card).find("Playback:") != string::npos;
}

// Collect USB card indexes from /proc/asound/cards
vector<int> listUsbCards() {
    vector<int> usbCards;
#ifdef __linux__
    ifstream cardsFile("/proc/asound/cards");
    if (!cardsFile) return usbCards;
    string line;
    while (getline(cardsFile, line)) {
        if (line.find("USB-Audio") == string::npos) continue;
        size_t start = line.find_first_not_of(" \t");
        if (start == string::npos) continue;
        usbCards.push_back(atoi(line.c_str() + start));
    }
#endif
    return usbCards;
}

// Collect USB capture cards from arecord -l, same as speak/talk.py
vector<int> listUsbCaptureCards() {
    vector<int> usbCards;
#ifdef __linux__
    FILE* pipe = popen("arecord -l 2>/dev/null", "r");
    if (!pipe) return usbCards;
    char buffer[512];
    while (fgets(buffer, sizeof(buffer), pipe)) {
        string line = buffer;
        if (line.find("card") != 0 || line.find("USB") == string::npos) continue;
        size_t numberStart = line.find_first_of("0123456789");
        if (numberStart == string::npos) continue;
        usbCards.push_back(atoi(line.c_str() + numberStart));
    }
    pclose(pipe);
#endif
    return usbCards;
}

// Prefer the USB audio dongle, skip camera-only capture cards
string findUsbMicDevice() {
    vector<int> usbCards = listUsbCaptureCards();
    for (int card : usbCards) {
        if (cardHasPlayback(card)) return "plughw:" + to_string(card) + ",0";
    }
    if (!usbCards.empty()) return "plughw:" + to_string(usbCards[0]) + ",0";
    return "";
}

// Prefer a playback-only USB speaker, same as talk.py find_usb_card
string findUsbSpeakerDevice() {
    vector<int> usbCards = listUsbCards();
    for (int card : usbCards) {
        if (!cardHasCapture(card)) return "plughw:" + to_string(card) + ",0";
    }
    if (!usbCards.empty()) return "plughw:" + to_string(usbCards[0]) + ",0";
    return "";
}

// Find USB ALSA plughw device, prefer a card with a mic
string findUsbAlsaDevice() {
    string mic = findUsbMicDevice();
    if (!mic.empty()) return mic;
    return findUsbSpeakerDevice();
}

// Collect Pulse source or sink names from pactl
vector<string> listPulseShortNames(const char* command) {
    vector<string> names;
#ifdef __linux__
    FILE* pipe = popen(command, "r");
    if (!pipe) return names;
    char buffer[512];
    while (fgets(buffer, sizeof(buffer), pipe)) {
        string line = buffer;
        size_t tab = line.find('\t');
        if (tab == string::npos) continue;
        size_t start = tab + 1;
        size_t end = line.find('\t', start);
        if (end == string::npos) end = line.find('\n', start);
        string name = line.substr(start, end - start);
        if (!name.empty()) names.push_back(name);
    }
    pclose(pipe);
#endif
    return names;
}

// True when this name is a Pulse USB device, not HDMI, monitor, or Arducam
bool isUsbPulseAudioName(const string& name) {
    if (name.find(".monitor") != string::npos) return false;
    if (name.find("Arducam") != string::npos) return false;
    if (name.find("hdmi") != string::npos || name.find("HDMI") != string::npos) return false;
    return name.find("usb-") != string::npos || name.find("USB") != string::npos;
}

// Prefer the USB dongle Pulse capture, skip the camera mic
string findUsbPulseSource() {
    for (const string& name : listPulseShortNames("pactl list short sources 2>/dev/null")) {
        if (name.find("alsa_input") != 0) continue;
        if (isUsbPulseAudioName(name)) return name;
    }
    return "";
}

// Prefer the USB dongle Pulse speaker
string findUsbPulseSink() {
    for (const string& name : listPulseShortNames("pactl list short sinks 2>/dev/null")) {
        if (name.find("alsa_output") != 0) continue;
        if (isUsbPulseAudioName(name)) return name;
    }
    return "";
}

// Set mic and speaker to the same ALSA device
void setVideoAudioDevice(string device) {
    if (device.empty()) return;
    setVideoAudioDevices(device, device);
}

// Set ALSA devices for call mic and speaker
void setVideoAudioDevices(string micDevice, string speakerDevice) {
    if (!micDevice.empty()) {
        videoMicDevice = micDevice;
        videoAudioDevice = micDevice;
    }
    if (!speakerDevice.empty()) videoSpeakerDevice = speakerDevice;
    cout << "Call mic:     " << videoMicDevice << endl;
    cout << "Call speaker: " << videoSpeakerDevice << endl;
}

// Mute call mic by sending silence instead of opening ALSA capture
void setVideoMuteMic(bool muteMic) {
    videoMuteMic = muteMic;
}

// Size, rate, and volume for remote websocket PCM, header is audi as int16 char codes
const int REMOTE_PCM_HEADER_BYTES = 8;
const int REMOTE_PCM_RATE = 48000;
const double REMOTE_PCM_VOLUME = 0.20;
const unsigned char REMOTE_PCM_HEADER[] = { 'a', 0, 'u', 0, 'd', 0, 'i', 0 };

// Open a speaker pipeline for raw PCM from any peer
void startRemotePcmPlayback() {
#ifdef HAVE_GSTREAMER_WEBRTC
    if (remotePcmPipeline) return;
    gst_init(NULL, NULL);

    // Play S16LE mono PCM on the USB speaker at 20 percent
    string sinkElement = isPulseAudioDevice(videoSpeakerDevice) ? "pulsesink" : "alsasink";
    string pipelineString = "appsrc name=pcmspeak is-live=true format=time do-timestamp=true block=false max-bytes=32768 ! "
        "queue leaky=downstream max-size-buffers=0 max-size-time=200000000 max-size-bytes=0 ! "
        "audioconvert ! audioresample ! audio/x-raw,rate=" + to_string(REMOTE_PCM_RATE) + ",channels=1 ! "
        "volume volume=" + to_string(REMOTE_PCM_VOLUME) + " ! "
        + sinkElement + " name=pcmsink device=" + videoSpeakerDevice + " sync=false buffer-time=" + to_string(VIDEO_AUDIO_BUFFER_TIME) + " latency-time=" + to_string(VIDEO_AUDIO_LATENCY_TIME);
    GError* error = NULL;
    remotePcmPipeline = gst_parse_launch(pipelineString.c_str(), &error);
    if (error) {
        cout << "Failed to start remote PCM playback: " << error->message << endl;
        g_error_free(error);
        if (remotePcmPipeline) gst_object_unref(remotePcmPipeline);
        remotePcmPipeline = NULL;
        return;
    }

    // Match the peer's raw PCM
    remotePcmSrc = gst_bin_get_by_name(GST_BIN(remotePcmPipeline), "pcmspeak");
    GstCaps* caps = gst_caps_new_simple("audio/x-raw", "format", G_TYPE_STRING, "S16LE", "layout", G_TYPE_STRING, "interleaved", "rate", G_TYPE_INT, REMOTE_PCM_RATE, "channels", G_TYPE_INT, 1, NULL);
    g_object_set(remotePcmSrc, "caps", caps, NULL);
    gst_caps_unref(caps);

    // Start the speaker
    GstStateChangeReturn result = gst_element_set_state(remotePcmPipeline, GST_STATE_PLAYING);
    if (result == GST_STATE_CHANGE_FAILURE) {
        cout << "Failed to play remote PCM on " << videoSpeakerDevice << endl;
        stopRemotePcmPlayback();
        return;
    }
    cout << "Remote PCM playback on " << videoSpeakerDevice << endl;
#endif
}

// Stop PCM playback from a remote peer
void stopRemotePcmPlayback() {
#ifdef HAVE_GSTREAMER_WEBRTC
    if (!remotePcmPipeline) return;
    gst_element_set_state(remotePcmPipeline, GST_STATE_NULL);
    if (remotePcmSrc) gst_object_unref(remotePcmSrc);
    gst_object_unref(remotePcmPipeline);
    remotePcmSrc = NULL;
    remotePcmPipeline = NULL;
#endif
}

// Play one websocket PCM packet, header is 4 little-endian int16 char codes for audi
bool playRemotePcmPacket(const char* data, size_t size) {
    if (!data || size < REMOTE_PCM_HEADER_BYTES + 2) return false;
    if (memcmp(data, REMOTE_PCM_HEADER, REMOTE_PCM_HEADER_BYTES) != 0) return false;
#ifdef HAVE_GSTREAMER_WEBRTC
    if (!remotePcmPipeline) startRemotePcmPlayback();
    if (!remotePcmSrc) return true;

    // Push the PCM after the audi header into the speaker pipeline
    size_t pcmBytes = size - REMOTE_PCM_HEADER_BYTES;
    GstBuffer* buffer = gst_buffer_new_allocate(NULL, pcmBytes, NULL);
    gst_buffer_fill(buffer, 0, data + REMOTE_PCM_HEADER_BYTES, pcmBytes);
    GstFlowReturn flow = gst_app_src_push_buffer(GST_APP_SRC(remotePcmSrc), buffer);
    if (flow != GST_FLOW_OK && flow != GST_FLOW_FLUSHING) {
        cout << "Remote PCM playback stalled, flow " << (int)flow << "." << endl;
    }
#else
    (void)size;
#endif
    return true;
}

#ifdef HAVE_GSTREAMER_WEBRTC
// True when call audio should go through Pulse
bool isPulseAudioDevice(const string& device) {
    return device.find("alsa_input") == 0 || device.find("alsa_output") == 0;
}

// Check GStreamer element
bool isGStreamerAudioElement(string name) {
    GstElementFactory* factory = gst_element_factory_find(name.c_str());
    if (!factory) return false;
    gst_object_unref(factory);
    return true;
}

// Report the webrtcdsp plugin version, 1.24 and up means the AEC3 canceller
string echoCancelVersion() {
    GstElementFactory* factory = gst_element_factory_find("webrtcdsp");
    if (!factory) return "none";
    GstPlugin* plugin = gst_plugin_feature_get_plugin(GST_PLUGIN_FEATURE(factory));
    gst_object_unref(factory);
    if (!plugin) return "unknown";
    const gchar* version = gst_plugin_get_version(plugin);
    string found = version ? version : "unknown";
    gst_object_unref(plugin);
    return found;
}

// Drop the known GStreamer-Audio channel-position critical during call setup
void quietGstAudioLog(const gchar* logDomain, GLogLevelFlags logLevel, const gchar* message, gpointer userData) {
    (void)logDomain;
    (void)userData;
    if (message && strstr(message, "gst_audio_ring_buffer_set_channel_positions")) return;
    g_log_default_handler("GStreamer-Audio", logLevel, message, NULL);
}

// Quiet expected audio setup noise
void quietCallAudioLog() {
    g_log_set_handler("GStreamer-Audio", (GLogLevelFlags)(G_LOG_LEVEL_CRITICAL | G_LOG_FLAG_FATAL), quietGstAudioLog, NULL);
}

// Create local microphone branch
string createAudioSourceString() {
    string source;
    if (videoMuteMic) {
        cout << "Call mic muted, sending silence." << endl;
        source = "audiotestsrc name=robotmic is-live=true wave=silence ! ";
    } else if (isPulseAudioDevice(videoMicDevice)) {
        cout << "Using Pulse mic " << videoMicDevice << " for call." << endl;
        source = "pulsesrc name=robotmic device=" + videoMicDevice + " buffer-time=" + to_string(VIDEO_AUDIO_BUFFER_TIME) + " latency-time=" + to_string(VIDEO_AUDIO_LATENCY_TIME) + " ! ";
    } else {
        cout << "Using ALSA mic " << videoMicDevice << " for call." << endl;
        source = "alsasrc name=robotmic device=" + videoMicDevice + " buffer-time=" + to_string(VIDEO_AUDIO_BUFFER_TIME) + " latency-time=" + to_string(VIDEO_AUDIO_LATENCY_TIME) + " ! ";
    }

    // Match the rate the canceller and its probe must share
    string process = "audioconvert name=robotaudioconvert ! audioresample name=robotaudioresample ! audio/x-raw,format=S16LE,rate=" + to_string(VIDEO_AUDIO_RATE) + ",channels=1 ! ";

    // Cancel speaker echo, and hold mic gain fixed so a quiet room cannot raise loop gain
    if (!videoMuteMic && isGStreamerAudioElement("webrtcdsp")) {
        cout << "Using WebRTC echo cancel on call mic, plugin " << echoCancelVersion() << "." << endl;
        process += "webrtcdsp name=robotdsp probe=robotechoprobe echo-cancel=true noise-suppression=true noise-suppression-level=high high-pass-filter=true gain-control=true gain-control-mode=fixed-digital compression-gain-db=" + to_string(CALL_MIC_GAIN_DB) + " target-level-dbfs=" + to_string(CALL_MIC_LIMIT_DBFS) + " limiter=true ! ";

        // Carve out every tone that starts to ring, these filters want float and only eight poles stay stable
        if (isGStreamerAudioElement("audiochebband")) {
            cout << "Using " << NOTCH_COUNT << " tunable notches on call mic." << endl;
            process += "audioconvert ! audio/x-raw,format=F32LE,rate=" + to_string(VIDEO_AUDIO_RATE) + ",channels=1 ! ";
            for (int index = 0; index < NOTCH_COUNT; index++) {
                process += "audiochebband name=robotnotch" + to_string(index) + " mode=band-reject poles=" + to_string(NOTCH_POLES) + " ripple=" + to_string(NOTCH_RIPPLE_DB) + " lower-frequency=" + to_string(NOTCH_PARK_LOW_HZ) + " upper-frequency=" + to_string(NOTCH_PARK_HIGH_HZ) + " ! ";
            }
            process += "audioconvert ! audio/x-raw,format=S16LE,rate=" + to_string(VIDEO_AUDIO_RATE) + ",channels=1 ! ";
        }
    }
    return source + process +
        "opusenc name=robotopus audio-type=voice frame-size=20 bitrate=32000 ! rtpopuspay name=robotopuspay pt=97 ! "
        "application/x-rtp,media=audio,encoding-name=OPUS,payload=97 ! sendrecv.";
}

// Add named echo probe so webrtcdsp can cancel speaker audio from the mic
void attachEchoCanceller(GstElement* pipeline) {
    if (!pipeline) return;
    GstElement* dsp = gst_bin_get_by_name(GST_BIN(pipeline), "robotdsp");
    if (!dsp) return;
    gst_object_unref(dsp);
    GstElement* existing = gst_bin_get_by_name(GST_BIN(pipeline), "robotechoprobe");
    if (existing) {
        gst_object_unref(existing);
        return;
    }
    GstElement* probe = gst_element_factory_make("webrtcechoprobe", "robotechoprobe");
    if (!probe) {
        cout << "Echo probe missing, speaker ducking only." << endl;
        return;
    }
    gst_bin_add(GST_BIN(pipeline), probe);
    gst_element_sync_state_with_parent(probe);
}

// Wire AEC and the feedback notches onto the live pipeline
void attachCallAudio(GstElement* pipeline, GMainLoop* loop) {
    audioDisconnectLogged = false;
    attachEchoCanceller(pipeline);

    // Hold every notch parked above hearing until a tone needs it
    for (int index = 0; index < NOTCH_COUNT; index++) {
        string name = "robotnotch" + to_string(index);
        notchElements[index] = gst_bin_get_by_name(GST_BIN(pipeline), name.c_str());
        notchHertz[index] = 0;
        notchHalfWidth[index] = NOTCH_HALF_WIDTH_HZ;
    }

    // Listen at the end of the notch chain, so a tone that survives its notch still shows
    micToneTapElement = notchElements[NOTCH_COUNT - 1];
    if (!micToneTapElement) return;
    cout << "Call audio notching feedback tones out of the mic." << endl;
    if (!toneFft) toneFft = gst_fft_f32_new(TONE_FFT_SIZE, FALSE);
    toneWindowFill = 0;
    toneStableFrames = 0;
    attachMicToneProbe(micToneTapElement);

    // Hand notches back on a slow tick once their tone has gone
    if (loop && !notchTimerId) {
        GSource* source = g_timeout_source_new(NOTCH_POLL_MS);
        g_source_set_callback(source, pollNotches, NULL, NULL);
        notchTimerId = g_source_attach(source, g_main_loop_get_context(loop));
        g_source_unref(source);
    }
}

// Drop call-audio state when the pipeline stops
void stopCallAudio() {
    lock_guard<mutex> lock(notchLock);
    if (notchTimerId) {
        g_source_remove(notchTimerId);
        notchTimerId = 0;
    }
    if (toneFft) gst_fft_f32_free(toneFft);
    toneFft = NULL;
    toneWindowFill = 0;
    toneStableFrames = 0;

    // Let the notches go with the pipeline that held them
    for (int index = 0; index < NOTCH_COUNT; index++) {
        if (notchElements[index]) gst_object_unref(notchElements[index]);
        notchElements[index] = NULL;
        notchHertz[index] = 0;
    }
    micToneTapElement = NULL;
    audioDisconnectLogged = false;
}

// Play remote audio on the USB speaker
void linkRemoteAudioPad(GstElement* pipeline, GstPad* pad) {
    GstElement* queue = gst_element_factory_make("queue", NULL);
    GstElement* convert = gst_element_factory_make("audioconvert", NULL);
    GstElement* resample = gst_element_factory_make("audioresample", NULL);
    GstElement* reference = gst_element_factory_make("capsfilter", "robotspeakercaps");
    GstElement* volume = gst_element_factory_make("volume", "robotspeakervolume");
    GstElement* sink = NULL;
    if (isPulseAudioDevice(videoSpeakerDevice)) {
        sink = gst_element_factory_make("pulsesink", NULL);
    } else {
        sink = gst_element_factory_make("alsasink", NULL);
    }
    if (!queue || !convert || !resample || !reference || !volume || !sink) {
        cout << "Failed to create remote audio sink." << endl;
        return;
    }

    // Hold every reference buffer, the canceller cannot align a stream with gaps in it
    g_object_set(queue, "max-size-buffers", 0, "max-size-time", (guint64)VIDEO_AUDIO_QUEUE_MS * GST_MSECOND, "max-size-bytes", 0, "leaky", 0, NULL);

    // Give the probe the only format it accepts, and let the sink clock playback so its timestamps stay usable
    GstCaps* referenceCaps = gst_caps_new_simple("audio/x-raw", "format", G_TYPE_STRING, "S16LE", "rate", G_TYPE_INT, VIDEO_AUDIO_RATE, "channels", G_TYPE_INT, 1, NULL);
    g_object_set(reference, "caps", referenceCaps, NULL);
    gst_caps_unref(referenceCaps);
    g_object_set(volume, "volume", VIDEO_SPEAKER_VOLUME, NULL);
    g_object_set(sink, "device", videoSpeakerDevice.c_str(), "sync", TRUE, "buffer-time", (gint64)VIDEO_AUDIO_BUFFER_TIME, "latency-time", (gint64)VIDEO_AUDIO_LATENCY_TIME, NULL);
    GstElement* probe = gst_bin_get_by_name(GST_BIN(pipeline), "robotechoprobe");
    gst_bin_add_many(GST_BIN(pipeline), queue, convert, resample, reference, volume, sink, NULL);
    gst_element_sync_state_with_parent(queue);
    gst_element_sync_state_with_parent(convert);
    gst_element_sync_state_with_parent(resample);
    gst_element_sync_state_with_parent(reference);
    gst_element_sync_state_with_parent(volume);
    gst_element_sync_state_with_parent(sink);
    gst_element_link_many(queue, convert, resample, reference, volume, NULL);
    if (probe) {
        gst_element_link_many(volume, probe, sink, NULL);
        gst_object_unref(probe);
        cout << "Speaker echo probe linked for AEC." << endl;
    } else {
        gst_element_link(volume, sink);
    }
    GstPad* sinkPad = gst_element_get_static_pad(queue, "sink");
    gst_pad_link(pad, sinkPad);
    gst_object_unref(sinkPad);
    cout << "Linked remote audio stream to " << videoSpeakerDevice << endl;
}

// True when a GStreamer message is about a yanked USB sound card
bool isCallAudioDisconnectMessage(const string& errorMessage, const string& debugMessage) {
    bool audioElement = debugMessage.find("alsasink") != string::npos ||
        debugMessage.find("alsasrc") != string::npos ||
        debugMessage.find("AlsaSink") != string::npos ||
        debugMessage.find("AlsaSrc") != string::npos ||
        debugMessage.find("robotmic") != string::npos ||
        debugMessage.find("gstalsasink") != string::npos ||
        debugMessage.find("gstalsasrc") != string::npos;
    return errorMessage.find("audio device") != string::npos ||
        errorMessage.find("has been disconnected") != string::npos ||
        audioElement ||
        (audioDisconnectLogged && errorMessage.find("Internal data stream error") != string::npos);
}

// Log a USB audio unplug once
bool shouldLogCallAudioDisconnect() {
    if (audioDisconnectLogged) return false;
    audioDisconnectLogged = true;
    return true;
}

// Feed outgoing mic buffers to the tone detector
GstPadProbeReturn onMicToneProbe(GstPad* pad, GstPadProbeInfo* info, gpointer userData) {
    (void)userData;
    if (!(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER)) return GST_PAD_PROBE_OK;
    GstBuffer* buffer = GST_PAD_PROBE_INFO_BUFFER(info);
    if (!buffer) return GST_PAD_PROBE_OK;
    appendToneSamples(pad, buffer);
    return GST_PAD_PROBE_OK;
}

// Attach the tone probe to an element's src pad
void attachMicToneProbe(GstElement* element) {
    GstPad* pad = gst_element_get_static_pad(element, "src");
    if (!pad) return;
    gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, onMicToneProbe, NULL, NULL);
    gst_object_unref(pad);
}

// Give notches back once their tone has stayed away
gboolean pollNotches(gpointer userData) {
    (void)userData;
    lock_guard<mutex> lock(notchLock);
    auto now = steady_clock::now();
    for (int index = 0; index < NOTCH_COUNT; index++) {
        if (notchHertz[index] == 0) continue;
        if (duration_cast<milliseconds>(now - notchSeenAt[index]).count() < NOTCH_HOLD_MS) continue;
        cout << "Tone at " << (int)notchHertz[index] << "Hz gone, clearing its notch." << endl;
        parkNotch(index);
    }
    return G_SOURCE_CONTINUE;
}

// Collect samples and look for a tone once per hop
void appendToneSamples(GstPad* pad, GstBuffer* buffer) {
    if (!toneFft) return;
    GstMapInfo map;
    if (!gst_buffer_map(buffer, &map, GST_MAP_READ)) return;
    GstCaps* caps = gst_pad_get_current_caps(pad);
    const char* format = NULL;
    if (caps && gst_caps_get_size(caps) > 0) {
        format = gst_structure_get_string(gst_caps_get_structure(caps, 0), "format");
    }
    bool isFloat = format && (g_str_equal(format, "F32LE") || g_str_equal(format, "F32BE"));
    int count = isFloat ? map.size / sizeof(float) : map.size / sizeof(gint16);
    const float* floatSamples = (const float*)map.data;
    const gint16* shortSamples = (const gint16*)map.data;

    // Fill the window, then step it on by one hop for each look
    for (int i = 0; i < count; i++) {
        toneWindow[toneWindowFill] = isFloat ? floatSamples[i] : shortSamples[i] / 32768.0f;
        toneWindowFill++;
        if (toneWindowFill < TONE_FFT_SIZE) continue;
        double hertz = 0;
        bool tone = detectTonePeak(hertz);
        memmove(toneWindow, toneWindow + TONE_HOP, (TONE_FFT_SIZE - TONE_HOP) * sizeof(float));
        toneWindowFill = TONE_FFT_SIZE - TONE_HOP;
        if (!tone) continue;
        lock_guard<mutex> lock(notchLock);
        notchMicTone(hertz);
    }
    if (caps) gst_caps_unref(caps);
    gst_buffer_unmap(buffer, &map);
}

// True when a narrow, loud, unmoving, non harmonic peak has held
bool detectTonePeak(double& hertz) {
    float windowed[TONE_FFT_SIZE];
    memcpy(windowed, toneWindow, sizeof(windowed));
    gst_fft_f32_window(toneFft, windowed, GST_FFT_WINDOW_HAMMING);
    GstFFTF32Complex spectrum[TONE_FFT_SIZE / 2 + 1];
    gst_fft_f32_fft(toneFft, windowed, spectrum);

    // Search only the band where feedback settles
    int firstBin = (int)(TONE_MIN_HZ * TONE_FFT_SIZE / VIDEO_AUDIO_RATE);
    int lastBin = (int)(TONE_MAX_HZ * TONE_FFT_SIZE / VIDEO_AUDIO_RATE);
    double levels[TONE_FFT_SIZE / 2 + 1];
    double peakDb = -120;
    int peakBin = firstBin;
    int count = 0;
    for (int bin = firstBin; bin <= lastBin; bin++) {
        double db = spectrumBinDb(spectrum, bin);
        levels[count] = db;
        count++;
        if (db > peakDb) {
            peakDb = db;
            peakBin = bin;
        }
    }

    // A tone stands far above the typical bin, speech does not
    sort(levels, levels + count);
    double dominance = peakDb - levels[count / 2];

    // A hum or a sung note carries harmonics, a feedback tone stands alone
    double harmonicDb = -120;
    if (peakBin * 2 <= TONE_FFT_SIZE / 2) harmonicDb = max(harmonicDb, spectrumBinDb(spectrum, peakBin * 2));
    if (peakBin / 2 >= 2) harmonicDb = max(harmonicDb, spectrumBinDb(spectrum, peakBin / 2));

    // Believe it only once the same bin has held for a while
    bool candidate = dominance > TONE_DOMINANCE_DB && peakDb > TONE_MIN_PEAK_DB && harmonicDb <= peakDb - TONE_HARMONIC_DB;
    if (!candidate) {
        toneStableFrames = 0;
        return false;
    }
    if (toneStableFrames > 0 && abs(peakBin - toneStableBin) > TONE_BIN_TOLERANCE) toneStableFrames = 0;
    toneStableBin = peakBin;
    toneStableFrames++;
    if (toneStableFrames < TONE_STABLE_FRAMES) return false;
    toneStableFrames = 0;
    hertz = (double)peakBin * VIDEO_AUDIO_RATE / TONE_FFT_SIZE;
    return true;
}

// Magnitude of one spectrum bin in dB
double spectrumBinDb(GstFFTF32Complex* spectrum, int bin) {
    double real = spectrum[bin].r;
    double imaginary = spectrum[bin].i;
    double power = real * real + imaginary * imaginary;
    if (power <= 0) return -120;
    return 10.0 * log10(power / (TONE_FFT_SIZE * TONE_FFT_SIZE / 4.0));
}

// Put a notch on a ringing tone, widening or reusing one when they run short
void notchMicTone(double hertz) {
    auto now = steady_clock::now();

    // A tone still ringing under its own notch needs that notch wider
    for (int index = 0; index < NOTCH_COUNT; index++) {
        if (notchHertz[index] == 0) continue;
        if (fabs(notchHertz[index] - hertz) > NOTCH_MATCH_HZ) continue;
        notchSeenAt[index] = now;
        if (duration_cast<milliseconds>(now - notchSetAt[index]).count() < NOTCH_SETTLE_MS) return;
        if (notchHalfWidth[index] >= NOTCH_MAX_HALF_WIDTH_HZ) return;
        engageNotch(index, notchHertz[index], notchHalfWidth[index] + NOTCH_WIDEN_HZ);
        return;
    }

    // Spend a spare notch on it, or take the one whose tone is longest gone
    for (int index = 0; index < NOTCH_COUNT; index++) {
        if (notchHertz[index] != 0) continue;
        engageNotch(index, hertz, NOTCH_HALF_WIDTH_HZ);
        return;
    }
    engageNotch(leastRecentNotch(), hertz, NOTCH_HALF_WIDTH_HZ);
}

// Point one notch at a tone and start its settling clock
void engageNotch(int index, double hertz, double halfWidth) {
    if (!notchElements[index]) return;
    double lower = hertz - halfWidth;
    double upper = hertz + halfWidth;
    g_object_set(notchElements[index], "lower-frequency", (gfloat)lower, "upper-frequency", (gfloat)upper, NULL);
    notchHertz[index] = hertz;
    notchHalfWidth[index] = halfWidth;
    notchSetAt[index] = steady_clock::now();
    notchSeenAt[index] = notchSetAt[index];
    cout << "Notching " << (int)hertz << "Hz out of the mic, " << (int)(halfWidth * 2) << "Hz wide." << endl;
}

// Send a notch back above hearing so it colours nothing
void parkNotch(int index) {
    if (!notchElements[index]) return;
    g_object_set(notchElements[index], "lower-frequency", (gfloat)NOTCH_PARK_LOW_HZ, "upper-frequency", (gfloat)NOTCH_PARK_HIGH_HZ, NULL);
    notchHertz[index] = 0;
    notchHalfWidth[index] = NOTCH_HALF_WIDTH_HZ;
}

// The notch whose tone was heard longest ago, the cheapest one to steal
int leastRecentNotch() {
    int oldest = 0;
    for (int index = 1; index < NOTCH_COUNT; index++) {
        if (notchSeenAt[index] < notchSeenAt[oldest]) oldest = index;
    }
    return oldest;
}

#endif
