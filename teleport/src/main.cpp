/*
 * Teleport
 * Version 1.2.0
 *
 * Main
 *
 * Copyright (C) Thomas Jacobs - All Rights Reserved. <hatsmagee@gmail.com>
 * Last updated August 2026
*/

// Includes
#include <chrono>
#include <ctime>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

// WebSockets
#include <ixwebsocket/IXNetSystem.h>
#include <ixwebsocket/IXSocket.h>
#include <ixwebsocket/IXWebSocket.h>

// Local
#include "video.h"
#include "call.h"
#include "config.h"
#include "interface.h"
#include "look.h"
#include "move.h"

// Namespace
using namespace std;
using namespace std::chrono;
using namespace std::this_thread;
using namespace ix;

// Configure defaults
const char* DEFAULT_SERVER_URL = "ws://server.teleportconnect.com:8080";
const char* LOCAL_SERVER_URL = "ws://127.0.0.1:8080";

// Configure camera
const char* DEFAULT_CAMERA_PATH = "0";

// Configure device ID
const char* DEVICE_NAME_PREFIX = "teleport";
const char* BINARY_NAME = "teleport";

// Configure connection settings
const int PING_INTERVAL_SECONDS = 1;
const int SOCKET_PING_SECONDS = 10;
const uint32_t RECONNECT_WAIT_MILLISECONDS = 10000;

// Give up and decline when a call rings this long with nobody answering
const int RING_TIMEOUT_SECONDS = 60;

// Configure audio device
const char* DEFAULT_AUDIO_DEVICE = "plughw:0,0";

// The locally built plugins, webrtcdsp there runs AEC3 while the system one only has the older AEC2
const char* DESKMAN_PLUGIN_PATH = "/usr/local/lib/deskman-gstreamer-1.0";

#ifdef __linux__
const char* DEFAULT_DISPLAY = ":0";
#endif

// Track runtime state
int deviceId = FALLBACK_DEVICE_ID;
int argumentDeviceId = 0;
string serverUrl = DEFAULT_SERVER_URL;
string cameraPath = DEFAULT_CAMERA_PATH;
string deviceName;
string callPeer;
string audioDevice = DEFAULT_AUDIO_DEVICE;
bool holdIncomingCall = false;
bool autoAnswerPending = false;
steady_clock::time_point autoAnswerAt;
steady_clock::time_point ringTimeoutAt;
string pendingVideoOffer;
string pendingCameraView;
TeleportConfig settings;
vector<string> listedPeers;
WebSocket webSocket;
time_t lastPing = 0;
bool running = true;

// Declare functions
int main(int argumentCount, char** argumentValues);
void parseArgs(int argumentCount, char** argumentValues);
void checkAlreadyRunning();
pid_t findOtherRunning();
void setupDisplayEnv();
void setupPluginPath();
void printHelp();
void initSignals();
void signalHandler(int signalNumber);
void connectWebSocket();
void runMainLoop();
void sendPing();
void handleWebSocketMessage(const WebSocketMessagePtr& message);
void handleTextMessage(const string& text);
void handleCommand(const string& command, const string& commandArgument, const string& secondCommandArgument, const string& target);
void pollInterfaceCommands();
void beginIncomingCall(const string& peer, const string& cameraView);
void pollRingingCall();
void acceptIncomingCall();
void cancelIncomingCall();

// Main
int main(int argumentCount, char** argumentValues) {
    // Parse arguments
    parseArgs(argumentCount, argumentValues);

    // Make sure only one running
    checkAlreadyRunning();

    // Read call settings
    settings = loadConfig();
    if (settings.autoAnswer > 0) cout << "Auto-answering calls after " << settings.autoAnswer << " seconds." << endl;
    else cout << "Auto-answer off, calls wait for Accept." << endl;

    // Name this device, the argument wins over config.json
    deviceId = argumentDeviceId > 0 ? argumentDeviceId : settings.deviceId;
    deviceName = DEVICE_NAME_PREFIX + to_string(deviceId);

    // Point HDMI video at the local screen when DISPLAY is unset
    setupDisplayEnv();

    // Find the locally built plugins before anything starts GStreamer
    setupPluginPath();

    // Prefer separate USB mic and speaker when using the default ALSA device
    if (audioDevice == DEFAULT_AUDIO_DEVICE) {
        string usbMic = findUSBPulseSource();
        string usbSpeaker = findUSBPulseSink();
        if (usbMic.empty()) usbMic = findUSBMicDevice();
        if (usbSpeaker.empty()) usbSpeaker = findUSBSpeakerDevice();
        if (usbMic.empty()) usbMic = audioDevice;
        if (usbSpeaker.empty()) usbSpeaker = usbMic;
        setVideoAudioDevices(usbMic, usbSpeaker);
    } else {
        setVideoAudioDevices(audioDevice, audioDevice);
    }

    // Say which echo canceller and notches a call will get
    logCallAudioStatus();

    // Init
    initSignals();
    initNetSystem();
    initVideoCamera(&webSocket, deviceName);
    initLook(&webSocket, deviceName);

    // Check video camera
    checkVideoCamera(cameraPath);
    startInterface();
    startCallInterface();

    // Connect
    connectWebSocket();

    // Run
    runMainLoop();

    // Done, stop video first so the peer can clear its window
    stopVideo();
    resumeInterfaceAfterCall();
    stopCallInterface();
    stopInterface();
    stopLook();
    stopMove();
    webSocket.stop();
    uninitNetSystem();

    // Return success
    return 0;
}

// Parse arguments
void parseArgs(int argumentCount, char** argumentValues) {
    // Parse simple flags
    for (int index = 1; index < argumentCount; index++) {
        string argument = argumentValues[index];

        // Show usage and exit
        if (argument == "--help" || argument == "-h") {
            printHelp();
            exit(0);
        }

        // Enable local server
        else if (argument == "--local") {
            serverUrl = LOCAL_SERVER_URL;
        }

        // Set device number, this beats the one in config.json
        else if (argument == "--device" && index + 1 < argumentCount) {
            argumentDeviceId = atoi(argumentValues[++index]);
        }

        // Set server URL
        else if (argument == "--server" && index + 1 < argumentCount) {
            serverUrl = argumentValues[++index];
        }

        // Set camera path
        else if (argument == "--camera" && index + 1 < argumentCount) {
            cameraPath = argumentValues[++index];
            #ifndef __APPLE__
                if (cameraPath.find("/") == string::npos) cameraPath = "/dev/video" + cameraPath;
            #endif
        }

        // Call another robot after login, e.g. --call teleport2
        else if (argument == "--call" && index + 1 < argumentCount) {
            callPeer = argumentValues[++index];
        }

        // Set ALSA audio device for robot calls
        else if (argument == "--audio-device" && index + 1 < argumentCount) {
            audioDevice = argumentValues[++index];
        }

        // Start muted, this is already the default
        else if (argument == "--mute" || argument == "--mute-mic") {
            setCallMicMuteDefault(true);
        }

        // Start with the mic on, skip the usual mute
        else if (argument == "--no-mute") {
            setCallMicMuteDefault(false);
        }
    }
}

// Make sure only one running
void checkAlreadyRunning() {
    pid_t existingPid = findOtherRunning();
    if (existingPid <= 0) return;
    cout << "Teleport already running, pid " << existingPid << endl;
    cout << "sudo service teleport stop" << endl;
    exit(1);
}

// Find another process
pid_t findOtherRunning() {
    FILE* pipe = popen("ps aux", "r");
    if (!pipe) return -1;

    // Skip the ps header, then parse pid and command
    pid_t myPid = getpid();
    char line[4096];
    bool header = true;
    pid_t foundPid = -1;
    while (fgets(line, sizeof(line), pipe)) {
        if (header) {
            header = false;
            continue;
        }

        // ps aux fields: USER PID ... TIME COMMAND
        stringstream stream(line);
        string user, cpu, memory, vsz, rss, tty, stat, start, time;
        pid_t pid = 0;
        if (!(stream >> user >> pid >> cpu >> memory >> vsz >> rss >> tty >> stat >> start >> time)) continue;
        if (pid == myPid) continue;

        // Command is the remainder of the line
        string command;
        getline(stream, command);
        if (!command.empty() && command[0] == ' ') command.erase(0, 1);

        // Match the teleport binary, not start_teleport.sh or journalctl
        stringstream commandStream(command);
        string executable;
        commandStream >> executable;
        size_t slash = executable.find_last_of('/');
        if (slash != string::npos) executable = executable.substr(slash + 1);
        if (executable == BINARY_NAME) {
            foundPid = pid;
            break;
        }
    }

    pclose(pipe);
    return foundPid;
}

// Point GStreamer at the local HDMI seat when launched from SSH
void setupDisplayEnv() {
#ifdef __linux__
    if (getenv("DISPLAY") == nullptr || getenv("DISPLAY")[0] == '\0') setenv("DISPLAY", DEFAULT_DISPLAY, 1);

    // Build the per-user runtime path
    string runtime = "/run/user/" + to_string(getuid());
    if (getenv("XDG_RUNTIME_DIR") == nullptr || getenv("XDG_RUNTIME_DIR")[0] == '\0') setenv("XDG_RUNTIME_DIR", runtime.c_str(), 1);

    // Point at the gdm Xauthority file when present
    if (getenv("XAUTHORITY") == nullptr || getenv("XAUTHORITY")[0] == '\0') {
        string xauth = runtime + "/gdm/Xauthority";
        if (access(xauth.c_str(), F_OK) == 0) setenv("XAUTHORITY", xauth.c_str(), 1);
    }
#endif
}

// Look for the locally built plugins, so a run from the shell gets the same AEC3 as the service does
void setupPluginPath() {
    if (access(DESKMAN_PLUGIN_PATH, F_OK) != 0) return;

    // Search ours first, keeping any path the caller set
    string path = DESKMAN_PLUGIN_PATH;
    const char* existing = getenv("GST_PLUGIN_PATH");
    if (existing && existing[0] && string(existing) != DESKMAN_PLUGIN_PATH) path += ":" + string(existing);
    setenv("GST_PLUGIN_PATH", path.c_str(), 1);
}

// Print command line usage
void printHelp() {
    cout << "Usage: teleport [options]" << endl;
    cout << endl;
    cout << "Options:" << endl;
    cout << "  -h, --help              Show this help" << endl;
    cout << "  --device <n>            Device number, login as teleport<n>" << endl;
    cout << "  --call <peer>           Call peer after login, e.g. teleport2" << endl;
    cout << "  --mute, --mute-mic      Start with the call mic muted, this is the default" << endl;
    cout << "  --no-mute               Start with the call mic on" << endl;
    cout << "  --server <url>          WebSocket server URL" << endl;
    cout << "  --local                 Use local server ws://127.0.0.1:8080" << endl;
    cout << "  --camera <path|n>       Camera path or index" << endl;
    cout << "  --audio-device <name>   ALSA device for robot calls" << endl;
}

// Run main loop
void runMainLoop() {
    // Keep process alive
    while (running) {
        sendPing();
        pollInterfaceCommands();
        pollLook();
        pollRingingCall();
        pollCallInterface();
        if (isCallOverlayIdle() && isVideoRunning()) stopVideo();
        sleep_for(milliseconds(16));
    }
}

// Send ping
void sendPing() {
    // Read current time
    time_t now = time(NULL);

    // Check ping interval
    if (now <= lastPing + PING_INTERVAL_SECONDS) return;

    // Send ping, with battery stats when the robot is up
    string ping = deviceName + " ping";
    float voltage = 0;
    int percent = 0;
    float current = 0;
    if (getInterfaceBattery(voltage, percent, current)) {
        char stats[64];
        snprintf(stats, sizeof(stats), " %.2f %d %.2f", voltage, percent, current);
        ping += stats;
    }
    ping += " ";
    webSocket.sendText(ping);
    lastPing = now;
}

// Handle websocket messages
void handleWebSocketMessage(const WebSocketMessagePtr& message) {
    // Log in on open
    if (message->type == WebSocketMessageType::Open) {
        cout << "Connected." << endl;
        webSocket.send(deviceName + " LOGIN teleport teleport");
    }

    // Log close
    else if (message->type == WebSocketMessageType::Close) {
        cout << "Connection closed." << endl;
    }

    // Log errors
    else if (message->type == WebSocketMessageType::Error) {
        cout << "Connection error: " << message->errorInfo.reason << endl;
    }

    // Route text messages
    else if (message->type == WebSocketMessageType::Message && !message->binary) {
        handleTextMessage(message->str);
    }
}

// Handle text messages
void handleTextMessage(const string& text) {
    // Parse message
    stringstream stream(text);
    string target;
    string command;
    getline(stream, target, ' ');
    getline(stream, command, ' ');
    string rest;
    getline(stream, rest);
    if (!rest.empty() && rest[0] == ' ') rest.erase(0, 1);
    string commandArgument = rest;
    string secondCommandArgument;
    size_t space = rest.find(' ');
    if (space != string::npos) {
        commandArgument = rest.substr(0, space);
        secondCommandArgument = rest.substr(space + 1);
    }

    // Log message, skip frequent signaling and keepalive noise
    if (command != "VIDEO_OFFER" && command != "VIDEO_ANSWER" && command != "VIDEO_ADDRESS" && command != "ping" && command != "Ping" && command != "x" && command != "y" && command != "hat") {
        cout << "Message: " << text << endl;
    }

    // Keep full JSON payload for video signaling
    if (command == "VIDEO_OFFER" || command == "VIDEO_ANSWER" || command == "VIDEO_ADDRESS") commandArgument = rest;

    // Handle command
    handleCommand(command, commandArgument, secondCommandArgument, target);
}

// Handle commands
void handleCommand(const string& command, const string& commandArgument, const string& secondCommandArgument, const string& target) {
    // Ignore unused command argument for now
    (void)secondCommandArgument;

    // Reply to device discovery
    if (command == "LIST") {
        webSocket.send(deviceName + " DEVICE " + deviceName);
    }

    // Confirm login
    else if (command == "LOGIN_OK") {
        cout << "Logged in." << endl;

        // Dial peer robot when --call was set
        if (!callPeer.empty()) {
            cout << "Calling " << callPeer << "..." << endl;
            webSocket.send(deviceName + " CONNECT " + callPeer);
            webSocket.send(deviceName + " Call");
            if (startCall(cameraPath)) showCallInProgress();
            else showCallFailed("Call not sent");
        }
    }

    // Reply to latency check
    else if (command == "Ping") {
        webSocket.send(deviceName + " Pong " + commandArgument);
    }

    // Ignore robot keepalive pings
    else if (command == "ping") {
    }

    // Look tilt
    else if (command == "x") {
        int value = atoi(commandArgument.c_str());
        move(command, value);
    }

    // Look pan
    else if (command == "y") {
        int value = atoi(commandArgument.c_str());
        move(command, value);
    }

    // Raise or lower the hat
    else if (command == "hat") {
        int value = atoi(commandArgument.c_str());
        move(command, value);
    }

    // Accept incoming robot call
    else if (command == "DEVICE") {
        if (commandArgument.empty() || commandArgument == deviceName) return;
        bool known = false;
        for (size_t index = 0; index < listedPeers.size(); index++) {
            if (listedPeers[index] == commandArgument) known = true;
        }
        if (!known) listedPeers.push_back(commandArgument);
        setCallPeers(listedPeers);
    }

    // Ring for a caller, both robots and browsers announce with Call
    else if (command == "Call") {
        if (holdIncomingCall || isVideoRunning()) return;
        string peer = commandArgument;
        if (peer.empty() && target != deviceName) peer = target;
        beginIncomingCall(peer, "");
    }

    // Ignore our own ringing notice coming back from the server
    else if (command == "RINGING") {
    }

    // Pick the camera view, callers send this alongside Call and the server repeats it
    else if (command == "StartVideo") {
        // Keep the view a ringing caller asked for, they send it right after Call
        if (holdIncomingCall) pendingCameraView = commandArgument;

        // Nothing reaches the camera without ringing first, so ring for a caller that skipped Call
        else if (!isVideoRunning()) beginIncomingCall(target, commandArgument);
    }

    // Stop video, or give up on a call that is still ringing
    else if (command == "StopVideo") {
        if (holdIncomingCall) {
            cout << "Caller hung up while ringing." << endl;
            cancelIncomingCall();
            showCallIdle();
            return;
        }
        stopVideo();
    }

    // Handle video signaling
    else if (command == "VIDEO_OFFER" || command == "VIDEO_ANSWER" || command == "VIDEO_ADDRESS" || command == "VIDEO_STOP") {
        // Hold an offer that arrives before the camera is open, ringing first if the caller skipped Call
        if (command == "VIDEO_OFFER" && !isVideoRunning()) {
            pendingVideoOffer = commandArgument;
            if (holdIncomingCall) cout << "Holding remote offer until Accept." << endl;
            else beginIncomingCall(target, "");
            return;
        }
        handleVideoMessage(command, commandArgument);
        if (command == "VIDEO_STOP") {
            cancelIncomingCall();
            showCallIdle();
        }
    }

    // Log unhandled command
    else if (!command.empty()) {
        cout << "Unhandled command: " << command << endl;
    }
}

// Apply Call / Hang up / List from the interface socket
void pollInterfaceCommands() {
    string command;
    string peer;
    if (!takeInterfaceCommand(command, peer) && !takeCallAction(command, peer)) return;

    if (command == "menu") {
        if (toggleCallDirectory()) {
            listedPeers.clear();
            setCallPeers(listedPeers);
            webSocket.send(deviceName + " LIST");
        }
    } else if (command == "call") {
        cout << "Calling " << peer << " from screen." << endl;
        cancelIncomingCall();
        webSocket.send(deviceName + " CONNECT " + peer);
        webSocket.send(deviceName + " Call");
        if (startCall(cameraPath)) {
            showCallInProgress();
        } else {
            webSocket.send(deviceName + " VIDEO_STOP");
            showCallFailed("Call not sent");
        }
    } else if (command == "hangup") {
        cancelIncomingCall();
        stopVideo();
        showCallIdle();
    } else if (command == "accept") {
        cout << "Accepting call." << endl;
        acceptIncomingCall();
    } else if (command == "decline") {
        cancelIncomingCall();
        webSocket.send(deviceName + " VIDEO_STOP");
        showCallIdle();
    } else if (command == "exit") {
        quitRobot();
        showCallIdle();
    }
}

// Ring the speaker and show the incoming bar until someone answers
void beginIncomingCall(const string& peer, const string& cameraView) {
    // Hold the caller off until Accept, or until the auto-answer wait runs out
    string caller = peer.empty() ? "peer" : peer;
    holdIncomingCall = true;
    pendingCameraView = cameraView;
    ringTimeoutAt = steady_clock::now() + seconds(RING_TIMEOUT_SECONDS);

    // Arm the auto-answer, or wait for a tap when it is switched off
    if (settings.autoAnswer > 0) {
        autoAnswerPending = true;
        autoAnswerAt = steady_clock::now() + seconds(settings.autoAnswer);
        cout << "Incoming call from " << caller << ", answering in " << settings.autoAnswer << " seconds." << endl;
    } else {
        autoAnswerPending = false;
        cout << "Incoming call from " << caller << ", waiting for Accept." << endl;
    }

    // Ring, and show the bar along the bottom of the screen
    showIncomingCall(caller);
    startRingtone();

    // Let the caller show that we are ringing
    webSocket.send(deviceName + " RINGING");
}

// Answer or give up on a ringing call once one of its waits runs out
void pollRingingCall() {
    if (!holdIncomingCall) return;

    // Answer alone when autoAnswer is set
    if (autoAnswerPending && steady_clock::now() >= autoAnswerAt) {
        cout << "Auto-answering call." << endl;
        acceptIncomingCall();
        return;
    }

    // Decline a call nobody has answered
    if (steady_clock::now() >= ringTimeoutAt) {
        cout << "Nobody answered, declining call." << endl;
        cancelIncomingCall();
        webSocket.send(deviceName + " VIDEO_STOP");
        showCallIdle();
    }
}

// Start the local side of an incoming call, robots and browsers answer the same way
void acceptIncomingCall() {
    holdIncomingCall = false;
    autoAnswerPending = false;
    stopRingtone();

    // Open the camera on the offer, so the encoder matches the payload type the caller asked for
    startVideo(cameraPath, pendingCameraView);

    // Answer the offer we held, the pipeline already has the candidates that followed it
    if (!pendingVideoOffer.empty()) {
        handleVideoMessage("VIDEO_OFFER", pendingVideoOffer);
        pendingVideoOffer.clear();
    }
}

// Drop a ringing incoming call
void cancelIncomingCall() {
    holdIncomingCall = false;
    autoAnswerPending = false;
    pendingVideoOffer.clear();
    clearVideoAddresses();
    stopRingtone();
}

// Initialize signals
void initSignals() {
    // Catch termination signals
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
}

// Handle signals
void signalHandler(int signalNumber) {
    // Keep this async-signal-safe: flag only, cleanup runs in main
    if (signalNumber == SIGINT || signalNumber == SIGTERM) running = false;
}

// Connect to web socket
void connectWebSocket() {
    // Cap the retry backoff, this setter takes milliseconds
    webSocket.setMaxWaitBetweenReconnectionRetries(RECONNECT_WAIT_MILLISECONDS);

    // Ping the server so a link that died without closing is noticed and retried
    webSocket.setPingInterval(SOCKET_PING_SECONDS);

    // Disable certificate files for compatibility with the old client setup
    SocketTLSOptions tls;
    tls.certFile = "NONE";
    tls.keyFile = "NONE";
    tls.caFile = "NONE";
    webSocket.setTLSOptions(tls);

    // Connect to server
    webSocket.setOnMessageCallback(handleWebSocketMessage);
    webSocket.setUrl(serverUrl);
    cout << "Connecting to server " << serverUrl << " as " << deviceName << "..." << endl;
    webSocket.start();
}
