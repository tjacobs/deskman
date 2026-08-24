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
#include "interface.h"
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
const int DEFAULT_DEVICE_ID = 1;
const char* DEVICE_ID_FILE = "device.id";
const char* BINARY_NAME = "teleport";

// Configure connection settings
const int PING_INTERVAL_SECONDS = 1;
const int SOCKET_PING_SECONDS = 10;
const uint32_t RECONNECT_WAIT_MILLISECONDS = 10000;
const int AUTO_ANSWER_DELAY_MS = 3000;

// Configure audio device
const char* DEFAULT_AUDIO_DEVICE = "plughw:0,0";

#ifdef __linux__
const char* DEFAULT_DISPLAY = ":0";
#endif

// Track runtime state
int deviceId = DEFAULT_DEVICE_ID;
string serverUrl = DEFAULT_SERVER_URL;
string cameraPath = DEFAULT_CAMERA_PATH;
string deviceName;
string callPeer;
string audioDevice = DEFAULT_AUDIO_DEVICE;
bool muteMic = false;
bool holdIncomingCall = false;
bool autoAnswerPending = false;
steady_clock::time_point autoAnswerAt;
string pendingVideoOffer;
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
void printHelp();
void initSignals();
void signalHandler(int signalNumber);
void connectWebSocket();
void runMainLoop();
void loadDeviceIdFromFile();
void sendPing();
void handleWebSocketMessage(const WebSocketMessagePtr& message);
void handleTextMessage(const string& text);
void handleCommand(const string& command, const string& commandArgument, const string& secondCommandArgument, const string& target);
void pollInterfaceCommands();
void pollAutoAnswer();
void acceptIncomingCall();
void cancelIncomingCall();

// Main
int main(int argumentCount, char** argumentValues) {
    // Parse arguments
    parseArgs(argumentCount, argumentValues);

    // Make sure only one running
    checkAlreadyRunning();

    // Point HDMI video at the local screen when DISPLAY is unset
    setupDisplayEnv();

    // Prefer separate USB mic and speaker when using the default ALSA device
    setVideoMuteMic(muteMic);
    if (muteMic) cout << "Call microphone muted." << endl;
    if (audioDevice == DEFAULT_AUDIO_DEVICE) {
        string usbMic = findUsbPulseSource();
        string usbSpeaker = findUsbPulseSink();
        if (usbMic.empty()) usbMic = findUsbMicDevice();
        if (usbSpeaker.empty()) usbSpeaker = findUsbSpeakerDevice();
        if (usbMic.empty()) usbMic = audioDevice;
        if (usbSpeaker.empty()) usbSpeaker = usbMic;
        setVideoAudioDevices(usbMic, usbSpeaker);
    } else {
        setVideoAudioDevices(audioDevice, audioDevice);
    }

    // Init
    initSignals();
    initNetSystem();
    initVideoCamera(&webSocket, deviceName);

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
    stopMove();
    webSocket.stop();
    uninitNetSystem();

    // Return success
    return 0;
}

// Parse arguments
void parseArgs(int argumentCount, char** argumentValues) {
    // Set defaults from code, then local device.id
    string name = "teleport";
    loadDeviceIdFromFile();
    deviceName = name + to_string(deviceId);

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

        // Set device number
        else if (argument == "--device" && index + 1 < argumentCount) {
            deviceId = atoi(argumentValues[++index]);
            deviceName = name + to_string(deviceId);
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

        // Send silence instead of microphone audio on calls
        else if (argument == "--mute" || argument == "--mute-mic") {
            muteMic = true;
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

// Print command line usage
void printHelp() {
    cout << "Usage: teleport [options]" << endl;
    cout << endl;
    cout << "Options:" << endl;
    cout << "  -h, --help              Show this help" << endl;
    cout << "  --device <n>            Device number, login as teleport<n>" << endl;
    cout << "  --call <peer>           Call peer after login, e.g. teleport2" << endl;
    cout << "  --mute, --mute-mic      Mute outbound call mic, send silence" << endl;
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
        pollAutoAnswer();
        pollCallInterface();
        if (isCallOverlayIdle() && isVideoRunning()) stopVideo();
        sleep_for(milliseconds(16));
    }
}

// Load machine-local device id from device.id when present
void loadDeviceIdFromFile() {
    // Read optional local override
    ifstream file(DEVICE_ID_FILE);
    if (!file.is_open()) return;
    int fileDeviceId = 0;
    file >> fileDeviceId;
    if (fileDeviceId > 0) deviceId = fileDeviceId;
}

// Send ping
void sendPing() {
    // Read current time
    time_t now = time(NULL);

    // Check ping interval
    if (now <= lastPing + PING_INTERVAL_SECONDS) return;

    // Send ping
    string ping = deviceName + " ping ";
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

    // Log binary messages
    else if (message->type == WebSocketMessageType::Message) {
        cout << "Binary message received, " << message->str.size() << " bytes" << endl;
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
            if (startCall(cameraPath, true)) showCallInProgress();
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

    // Ignore legacy web commands
    else if (command == "Start" || command == "Stop" || command == "ListTracks") {
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

    else if (command == "Call") {
        if (autoAnswerPending) return;
        string peer = commandArgument;
        if (peer.empty() && target != deviceName) peer = target;
        cout << "Incoming call from " << (peer.empty() ? "peer" : peer) << ", answering in 3 seconds." << endl;
        holdIncomingCall = true;
        autoAnswerPending = true;
        autoAnswerAt = steady_clock::now() + milliseconds(AUTO_ANSWER_DELAY_MS);
        showIncomingCall(peer.empty() ? "peer" : peer);
    }

    // Start video
    else if (command == "StartVideo") {
        startVideo(cameraPath, commandArgument);
    }

    // Stop video
    else if (command == "StopVideo") {
        stopVideo();
    }

    // Handle video signaling
    else if (command == "VIDEO_OFFER" || command == "VIDEO_ANSWER" || command == "VIDEO_ADDRESS" || command == "VIDEO_STOP") {
        if (command == "VIDEO_OFFER" && holdIncomingCall) {
            pendingVideoOffer = commandArgument;
            cout << "Holding remote offer until Accept." << endl;
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
        if (startCall(cameraPath, true)) {
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
    }
}

// Answer after the auto-answer delay
void pollAutoAnswer() {
    if (!autoAnswerPending) return;
    if (steady_clock::now() < autoAnswerAt) return;
    cout << "Auto-answering call." << endl;
    acceptIncomingCall();
}

// Start the local side of an incoming call
void acceptIncomingCall() {
    holdIncomingCall = false;
    autoAnswerPending = false;
    if (startCall(cameraPath, false)) {
        if (!pendingVideoOffer.empty()) {
            handleVideoMessage("VIDEO_OFFER", pendingVideoOffer);
            pendingVideoOffer.clear();
        }
        showCallInProgress();
    } else {
        pendingVideoOffer.clear();
        webSocket.send(deviceName + " VIDEO_STOP");
        showCallFailed("Call not sent");
    }
}

// Drop a ringing incoming call
void cancelIncomingCall() {
    holdIncomingCall = false;
    autoAnswerPending = false;
    pendingVideoOffer.clear();
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
