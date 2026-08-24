// Deskman robot control socket.
// Local Unix socket so Python can move the head while C++ owns the servos.

// Local headers
#include "control.h"
#include "servos.h"

// Third-party
#include <nlohmann/json.hpp>

// C++ standard library
#include <atomic>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

// POSIX sockets
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

// Shorthand for JSON and std names used below
using json = nlohmann::json;
using namespace std;

// Tilt and hat steps on the -100..100 command scale
const int HEAD_PERCENT_TILT_STEP = 20;
const int HEAD_PERCENT_HAT_STEP = 20;

// True while the accept loop should keep running
static atomic<bool> g_control_running{false};
static int g_listen_fd = -1;
static thread g_control_thread;
static string g_socket_path;

// Current commanded pan, tilt, and hat as a JSON reply
static json position_reply() {
    int pan = 0;
    int tilt = 0;
    int hat = 0;
    get_percent(pan, tilt, hat);
    return {{"ok", true}, {"x", pan}, {"y", tilt}, {"hat", hat}};
}

// Handle one JSON request, return JSON reply
static string handle_request(const string& line) {
    json reply;
    try {
        json request = json::parse(line);
        string command = request.value("command", "");

        if (command == "center") {
            center();
            reply = position_reply();
        } else if (command == "get") {
            reply = position_reply();
        } else if (command == "move") {
            if (request.contains("direction")) {
                string direction = request["direction"].get<string>();
                double degrees = request.value("degrees", 60.0);
                if (direction == "center" || direction == "left" || direction == "right") {
                    look_head(direction, degrees);
                } else if (direction == "up") {
                    move_percent(0, HEAD_PERCENT_TILT_STEP, 0);
                } else if (direction == "down") {
                    move_percent(0, -HEAD_PERCENT_TILT_STEP, 0);
                } else if (direction == "raise") {
                    move_percent(0, 0, -HEAD_PERCENT_HAT_STEP);
                } else if (direction == "lower") {
                    move_percent(0, 0, HEAD_PERCENT_HAT_STEP);
                } else {
                    reply = {{"ok", false}, {"error", "unknown direction"}};
                    return reply.dump();
                }
            } else if (request.contains("x") || request.contains("y") || request.contains("hat")) {
                int pan = 0;
                int tilt = 0;
                int hat = 0;
                get_percent(pan, tilt, hat);
                if (request.contains("x")) pan = request["x"].get<int>();
                if (request.contains("y")) tilt = request["y"].get<int>();
                if (request.contains("hat")) hat = request["hat"].get<int>();
                set_percent(pan, tilt, hat);
            } else {
                int pan_diff = request.value("dx", 0);
                int tilt_diff = request.value("dy", 0);
                int hat_diff = request.value("dhat", 0);
                move_percent(pan_diff, tilt_diff, hat_diff);
            }
            reply = position_reply();
        } else {
            reply = {{"ok", false}, {"error", "unknown command"}};
        }
    } catch (const exception& error) {
        reply = {{"ok", false}, {"error", error.what()}};
    }
    return reply.dump();
}

// Serve one accepted client until disconnect
static void serve_client(int client_fd) {
    string buffer;
    char chunk[1024];
    while (g_control_running.load()) {
        ssize_t n = read(client_fd, chunk, sizeof(chunk));
        if (n <= 0) break;
        buffer.append(chunk, static_cast<size_t>(n));

        // Process complete lines
        size_t pos;
        while ((pos = buffer.find('\n')) != string::npos) {
            string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            if (line.empty()) continue;
            string reply = handle_request(line);
            reply.push_back('\n');
            if (write(client_fd, reply.data(), reply.size()) < 0) {
                close(client_fd);
                return;
            }
        }
    }
    close(client_fd);
}

// Accept loop for the control socket
static void control_loop() {
    while (g_control_running.load()) {
        int client_fd = accept(g_listen_fd, nullptr, nullptr);
        if (client_fd < 0) {
            if (!g_control_running.load()) break;
            continue;
        }
        serve_client(client_fd);
    }
}

bool start_control(const string& socket_path) {
    if (g_control_running.load()) return true;

    // Remove stale socket file
    unlink(socket_path.c_str());

    // Create Unix domain socket
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (g_listen_fd < 0) {
        perror("control socket");
        return false;
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (socket_path.size() >= sizeof(addr.sun_path)) {
        cerr << "control: socket path too long" << endl;
        close(g_listen_fd);
        g_listen_fd = -1;
        return false;
    }
    strncpy(addr.sun_path, socket_path.c_str(), sizeof(addr.sun_path) - 1);

    if (bind(g_listen_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("control bind");
        close(g_listen_fd);
        g_listen_fd = -1;
        return false;
    }

    if (listen(g_listen_fd, 4) < 0) {
        perror("control listen");
        close(g_listen_fd);
        g_listen_fd = -1;
        unlink(socket_path.c_str());
        return false;
    }

    g_socket_path = socket_path;
    g_control_running = true;
    g_control_thread = thread(control_loop);
    // Socket ready for look tool and other clients
    return true;
}

void stop_control() {
    if (!g_control_running.load() && g_listen_fd < 0) return;

    g_control_running = false;
    if (g_listen_fd >= 0) {
        shutdown(g_listen_fd, SHUT_RDWR);
        close(g_listen_fd);
        g_listen_fd = -1;
    }
    if (g_control_thread.joinable()) g_control_thread.join();
    if (!g_socket_path.empty()) {
        unlink(g_socket_path.c_str());
        g_socket_path.clear();
    }
}
