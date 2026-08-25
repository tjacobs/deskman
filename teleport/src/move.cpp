/*
 * Send look commands to the socket.
*/

#include "move.h"

#include <chrono>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

using namespace std;
using namespace std::chrono;

static const char* ROBOT_INTERFACE_NAME = "robot.interface";
const int HEAD_STEP = 8;
const int HEAD_HAT_STEP = 20;
const int HEAD_REPEAT_MS = 100;

static mutex headMutex;
static int headSocketFd = -1;
static int lastX = 0;
static int lastY = 0;
static int lastHat = 0;
static steady_clock::time_point lastMoveX;
static steady_clock::time_point lastMoveY;
static steady_clock::time_point lastMoveHat;

static bool connectHead();
static void closeHead();
static bool writeHeadLine(const string& line);
static string robot_interface_path();

// Send web x, y, and hat to Deskman as one pan, tilt, or hat nudge
void move(const string& command, int value) {
    string axis;
    int* lastValue = NULL;
    steady_clock::time_point* lastMove = NULL;
    int step = HEAD_STEP;
    if (command == "x") {
        axis = "dy";
        lastValue = &lastX;
        lastMove = &lastMoveX;
    } else if (command == "y") {
        axis = "dx";
        lastValue = &lastY;
        lastMove = &lastMoveY;
    } else if (command == "hat") {
        axis = "dhat";
        lastValue = &lastHat;
        lastMove = &lastMoveHat;
        step = HEAD_HAT_STEP;
    } else {
        return;
    }

    lock_guard<mutex> lock(headMutex);

    // Keep stepping while held, the web repeats x, y, and hat every 100ms
    if (value == 0) {
        *lastValue = 0;
        return;
    }
    auto now = steady_clock::now();
    if (*lastValue != 0 && now - *lastMove < milliseconds(HEAD_REPEAT_MS)) return;
    *lastValue = value;
    *lastMove = now;

    // Web sends 1 or 100, both mean one step. Hat up is negative percent
    int delta = value > 0 ? step : -step;
    if (axis == "dhat") delta = -delta;
    string line = "{\"command\":\"move\",\"" + axis + "\":" + to_string(delta) + "}";
    if (!writeHeadLine(line)) {
        closeHead();
        if (connectHead()) writeHeadLine(line);
    }
}

void stopMove() {
    lock_guard<mutex> lock(headMutex);
    closeHead();
}

// Connect to Deskman's control socket
static bool connectHead() {
    if (headSocketFd >= 0) return true;
    int socketFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (socketFd < 0) return false;

    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    string path = robot_interface_path();
    if (path.size() >= sizeof(address.sun_path)) {
        close(socketFd);
        return false;
    }
    strncpy(address.sun_path, path.c_str(), sizeof(address.sun_path) - 1);
    if (connect(socketFd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        close(socketFd);
        return false;
    }

    // Fail a stuck read instead of blocking the websocket thread
    timeval timeout;
    timeout.tv_sec = 0;
    timeout.tv_usec = 200000;
    setsockopt(socketFd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(socketFd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    headSocketFd = socketFd;
    return true;
}

// Close the Deskman socket
static void closeHead() {
    if (headSocketFd < 0) return;
    close(headSocketFd);
    headSocketFd = -1;
}

// Write one JSON line and drop the reply
static bool writeHeadLine(const string& line) {
    if (headSocketFd < 0) return false;
    string payload = line + "\n";
    if (write(headSocketFd, payload.data(), payload.size()) != (ssize_t)payload.size()) return false;

    // Drain the ok reply so the socket does not back up
    char reply[256];
    ssize_t size = read(headSocketFd, reply, sizeof(reply));
    return size > 0;
}

// Same runtime directory robot uses for robot.interface
static string robot_interface_path() {
    string directory;
    const char* runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime && runtime[0]) directory = runtime;
    else directory = string("/run/user/") + to_string(getuid());
    struct stat info;
    if (stat(directory.c_str(), &info) != 0 || !S_ISDIR(info.st_mode)) directory = "/tmp";
    return directory + "/" + ROBOT_INTERFACE_NAME;
}
