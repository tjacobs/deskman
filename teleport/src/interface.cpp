/*
 * Connect to robot.interface to pause the camera, resume it, and receive menu taps.
*/

#include "interface.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

using namespace std;
using namespace std::chrono;

static const char* ROBOT_INTERFACE_NAME = "robot.interface";
const int INTERFACE_TIMEOUT_SECONDS = 10;
const int INTERFACE_RECONNECT_SECONDS = 2;
const int INTERFACE_BATTERY_TIMEOUT_SECONDS = 1;

static mutex interfaceMutex;
static condition_variable interfaceReplyCv;
static int clientSocketFd = -1;
static atomic<bool> interfaceRunning{false};
static thread interfaceThread;
static bool interfacePaused = false;
static bool waitingReply = false;
static string lastReply;
static string pendingCommand;
static string pendingPeer;

static string robotInterfacePath();
static bool connectRobot();
static void closeClient();
static void runInterface();
static bool writeInterfaceLine(string line);
static void handleInterfaceLine(string line);
static bool waitInterfaceOk();
static bool waitInterfaceReply(int timeoutSeconds);
static bool jsonNumber(const string& line, const string& key, float& value);
static void logError(const string& message);

void startInterface() {
    if (interfaceRunning.load()) return;
    interfaceRunning = true;
    interfaceThread = thread(runInterface);
}

void stopInterface() {
    interfaceRunning = false;
    closeClient();
    if (interfaceThread.joinable()) interfaceThread.join();
}

bool isInterfaceConnected() {
    lock_guard<mutex> lock(interfaceMutex);
    return clientSocketFd >= 0;
}

static void logError(const string& message) {
    if (isatty(STDERR_FILENO)) {
        cerr << "\033[1;31m*** ERROR *** : " << message << "\033[0m" << endl;
        return;
    }

    // systemd strips this prefix and colors the line when priority is err
    cerr << "<3>*** ERROR *** : " << message << endl;
}

void pauseInterfaceForCall() {
    if (interfacePaused) return;

    // Ask robot to drop the camera before this program opens it
    if (!writeInterfaceLine("{\"command\":\"pause\"}")) {
        logError("Could not request the camera, robot not connected.");
        return;
    }
    if (!waitInterfaceOk()) {
        logError("Could not request the camera, robot did not pause.");
        return;
    }
    interfacePaused = true;
}

void resumeInterfaceAfterCall() {
    if (!interfacePaused) return;
    if (!writeInterfaceLine("{\"command\":\"resume\"}")) cout << "Interface resume failed." << endl;
    else if (!waitInterfaceOk()) cout << "Interface resume failed." << endl;
    interfacePaused = false;
}

bool takeInterfaceCommand(string& command, string& peer) {
    lock_guard<mutex> lock(interfaceMutex);
    if (pendingCommand.empty()) return false;
    command = pendingCommand;
    peer = pendingPeer;
    pendingCommand.clear();
    pendingPeer.clear();
    return true;
}

// Ask robot for voltage, percent, and current
bool getInterfaceBattery(float& voltage, int& percent, float& current) {
    if (!writeInterfaceLine("{\"command\":\"battery\"}")) return false;
    if (!waitInterfaceReply(INTERFACE_BATTERY_TIMEOUT_SECONDS)) return false;

    // Copy the reply, then pull the three numbers
    string reply;
    {
        lock_guard<mutex> lock(interfaceMutex);
        reply = lastReply;
    }
    float percentValue = 0;
    if (!jsonNumber(reply, "voltage", voltage)) return false;
    if (!jsonNumber(reply, "percent", percentValue)) return false;
    if (!jsonNumber(reply, "current", current)) return false;
    percent = (int)(percentValue + 0.5f);
    return voltage > 0;
}

static string robotInterfacePath() {
    string directory;
    const char* runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime && runtime[0]) directory = runtime;
    else directory = string("/run/user/") + to_string(getuid());
    struct stat info;
    if (stat(directory.c_str(), &info) != 0 || !S_ISDIR(info.st_mode)) directory = "/tmp";
    return directory + "/" + ROBOT_INTERFACE_NAME;
}

static void closeClient() {
    lock_guard<mutex> lock(interfaceMutex);
    if (clientSocketFd >= 0) {
        shutdown(clientSocketFd, SHUT_RDWR);
        close(clientSocketFd);
        clientSocketFd = -1;
    }
    interfaceReplyCv.notify_all();
}

static bool writeInterfaceLine(string line) {
    if (line.empty() || line.back() != '\n') line.push_back('\n');
    lock_guard<mutex> lock(interfaceMutex);
    if (clientSocketFd < 0) return false;
    return write(clientSocketFd, line.data(), line.size()) >= 0;
}

static bool waitInterfaceOk() {
    return waitInterfaceReply(INTERFACE_TIMEOUT_SECONDS);
}

static bool waitInterfaceReply(int timeoutSeconds) {
    unique_lock<mutex> lock(interfaceMutex);
    waitingReply = true;
    lastReply.clear();
    time_t deadline = time(NULL) + timeoutSeconds;
    while (waitingReply && time(NULL) <= deadline) {
        interfaceReplyCv.wait_for(lock, chrono::seconds(1));
        if (!waitingReply) break;
    }
    waitingReply = false;
    return lastReply.find("\"ok\":true") != string::npos || lastReply.find("\"ok\": true") != string::npos;
}

static bool jsonNumber(const string& line, const string& key, float& value) {
    string needle = "\"" + key + "\":";
    size_t start = line.find(needle);
    if (start == string::npos) return false;
    start += needle.size();
    while (start < line.size() && line[start] == ' ') start++;
    char* end = NULL;
    value = strtof(line.c_str() + start, &end);
    return end != line.c_str() + start;
}

static void handleInterfaceLine(string line) {
    if (line.find("\"ok\"") != string::npos) {
        lock_guard<mutex> lock(interfaceMutex);
        lastReply = line;
        waitingReply = false;
        interfaceReplyCv.notify_all();
        return;
    }

    string command;
    string peer;
    const string commandKey = "\"command\":\"";
    size_t commandStart = line.find(commandKey);
    if (commandStart != string::npos) {
        commandStart += commandKey.size();
        size_t commandEnd = line.find("\"", commandStart);
        if (commandEnd != string::npos) command = line.substr(commandStart, commandEnd - commandStart);
    }
    const string peerKey = "\"peer\":\"";
    size_t peerStart = line.find(peerKey);
    if (peerStart != string::npos) {
        peerStart += peerKey.size();
        size_t peerEnd = line.find("\"", peerStart);
        if (peerEnd != string::npos) peer = line.substr(peerStart, peerEnd - peerStart);
    }
    if (command.empty()) return;
    lock_guard<mutex> lock(interfaceMutex);
    pendingCommand = command;
    pendingPeer = peer;
}

static bool connectRobot() {
    int socketFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (socketFd < 0) return false;
    string path = robotInterfacePath();
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    if (path.size() >= sizeof(address.sun_path)) {
        close(socketFd);
        return false;
    }
    strncpy(address.sun_path, path.c_str(), sizeof(address.sun_path) - 1);
    if (connect(socketFd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        close(socketFd);
        return false;
    }
    lock_guard<mutex> lock(interfaceMutex);
    if (clientSocketFd >= 0) close(clientSocketFd);
    clientSocketFd = socketFd;
    cout << "Connected to robot interface." << endl;
    return true;
}

static void runInterface() {
    cout << "Robot interface: " << robotInterfacePath() << endl;
    string buffer;
    while (interfaceRunning.load()) {
        int clientFd;
        {
            lock_guard<mutex> lock(interfaceMutex);
            clientFd = clientSocketFd;
        }
        if (clientFd < 0) {
            if (!connectRobot()) {
                sleep(INTERFACE_RECONNECT_SECONDS);
                continue;
            }
            buffer.clear();
            continue;
        }

        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(clientFd, &readSet);
        timeval timeout;
        timeout.tv_sec = 1;
        timeout.tv_usec = 0;
        int ready = select(clientFd + 1, &readSet, NULL, NULL, &timeout);
        if (ready <= 0) continue;

        char chunk[1024];
        ssize_t n = read(clientFd, chunk, sizeof(chunk));
        if (n <= 0) {
            closeClient();
            continue;
        }
        buffer.append(chunk, (size_t)n);
        size_t pos;
        while ((pos = buffer.find('\n')) != string::npos) {
            string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            if (!line.empty()) handleInterfaceLine(line);
        }
    }
}
