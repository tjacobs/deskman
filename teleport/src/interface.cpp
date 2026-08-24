/*
 * Unix socket for other programs to drive calls and receive pause/resume.
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

const char* INTERFACE_SOCKET_NAME = "teleport.interface";
const int INTERFACE_TIMEOUT_SECONDS = 10;
const int INTERFACE_ACCEPT_SECONDS = 1;

static mutex interfaceMutex;
static condition_variable interfaceReplyCv;
static int listenSocketFd = -1;
static int clientSocketFd = -1;
static atomic<bool> interfaceRunning{false};
static thread interfaceThread;
static bool interfacePaused = false;
static bool waitingReply = false;
static string lastReply;
static string pendingCommand;
static string pendingPeer;
static string interfaceSocketPath;

static string interfacePath();
static bool openListenSocket();
static void closeListenSocket();
static void closeClient();
static void runInterface();
static bool writeInterfaceLine(string line);
static void handleInterfaceLine(string line);
static bool waitInterfaceOk();
static void serveClient(int clientFd);

void startInterface() {
    if (interfaceRunning.load()) return;
    interfaceRunning = true;
    interfaceThread = thread(runInterface);
}

void stopInterface() {
    interfaceRunning = false;
    closeClient();
    closeListenSocket();
    if (interfaceThread.joinable()) interfaceThread.join();
}

bool isInterfaceConnected() {
    lock_guard<mutex> lock(interfaceMutex);
    return clientSocketFd >= 0;
}

void pauseInterfaceForCall() {
    if (interfacePaused) return;

    // Pause Deskman when it is listening, skip quietly if it is not
    if (!writeInterfaceLine("{\"command\":\"pause\"}")) return;
    if (!waitInterfaceOk()) return;
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

static string interfacePath() {
    string directory;
    const char* runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime && runtime[0]) directory = runtime;
    else directory = string("/run/user/") + to_string(getuid());
    struct stat info;
    if (stat(directory.c_str(), &info) != 0 || !S_ISDIR(info.st_mode)) directory = "/tmp";
    return directory + "/" + INTERFACE_SOCKET_NAME;
}

static bool openListenSocket() {
    interfaceSocketPath = interfacePath();
    unlink(interfaceSocketPath.c_str());
    int socketFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (socketFd < 0) return false;
    sockaddr_un address;
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (interfaceSocketPath.size() >= sizeof(address.sun_path)) {
        close(socketFd);
        return false;
    }
    strncpy(address.sun_path, interfaceSocketPath.c_str(), sizeof(address.sun_path) - 1);
    if (bind(socketFd, (sockaddr*)&address, sizeof(address)) < 0) {
        close(socketFd);
        return false;
    }
    chmod(interfaceSocketPath.c_str(), 0600);
    if (listen(socketFd, 4) < 0) {
        close(socketFd);
        unlink(interfaceSocketPath.c_str());
        return false;
    }
    listenSocketFd = socketFd;
    cout << "Interface:    " << interfaceSocketPath << endl;
    return true;
}

static void closeListenSocket() {
    if (listenSocketFd >= 0) {
        shutdown(listenSocketFd, SHUT_RDWR);
        close(listenSocketFd);
        listenSocketFd = -1;
    }
    if (!interfaceSocketPath.empty()) {
        unlink(interfaceSocketPath.c_str());
        interfaceSocketPath.clear();
    }
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
    unique_lock<mutex> lock(interfaceMutex);
    waitingReply = true;
    lastReply.clear();
    time_t deadline = time(NULL) + INTERFACE_TIMEOUT_SECONDS;
    while (waitingReply && time(NULL) <= deadline) {
        interfaceReplyCv.wait_for(lock, chrono::seconds(1));
        if (!waitingReply) break;
    }
    waitingReply = false;
    return lastReply.find("\"ok\":true") != string::npos || lastReply.find("\"ok\": true") != string::npos;
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

static void serveClient(int clientFd) {
    string buffer;
    while (interfaceRunning.load()) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(clientFd, &readSet);
        timeval timeout;
        timeout.tv_sec = INTERFACE_ACCEPT_SECONDS;
        timeout.tv_usec = 0;
        int ready = select(clientFd + 1, &readSet, NULL, NULL, &timeout);
        if (ready < 0) break;
        if (ready == 0) continue;
        char chunk[1024];
        ssize_t n = read(clientFd, chunk, sizeof(chunk));
        if (n <= 0) break;
        buffer.append(chunk, (size_t)n);
        size_t pos;
        while ((pos = buffer.find('\n')) != string::npos) {
            string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            if (!line.empty()) handleInterfaceLine(line);
        }
    }
}

static void runInterface() {
    if (!openListenSocket()) {
        cout << "Interface socket failed." << endl;
        return;
    }
    while (interfaceRunning.load()) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(listenSocketFd, &readSet);
        timeval timeout;
        timeout.tv_sec = INTERFACE_ACCEPT_SECONDS;
        timeout.tv_usec = 0;
        int ready = select(listenSocketFd + 1, &readSet, NULL, NULL, &timeout);
        if (ready <= 0) continue;
        int clientFd = accept(listenSocketFd, NULL, NULL);
        if (clientFd < 0) continue;
        {
            lock_guard<mutex> lock(interfaceMutex);
            if (clientSocketFd >= 0) close(clientSocketFd);
            clientSocketFd = clientFd;
        }
        cout << "Interface client connected." << endl;
        serveClient(clientFd);
        closeClient();
    }
    closeListenSocket();
}
