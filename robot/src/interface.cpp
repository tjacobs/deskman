// Unix socket for other programs to move the head, pause the camera, and open the menu.

#include "interface.h"
#include "servos.h"
#include "battery.h"
#include "screen.h"
#include "config.h"

// Main quit flag, set by Ctrl-C and the Exit button
extern volatile bool g_quit;

#include "json.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

using json = nlohmann::json;
using namespace std;
using namespace std::chrono;

const int HEAD_PERCENT_TILT_STEP = 20;
const int HEAD_PERCENT_HAT_STEP = 20;
const int CALL_HANDOFF_WAIT_SECONDS = 8;
const int CALL_MENU_TAP_DEBOUNCE_MS = 300;
static const char* ROBOT_INTERFACE_NAME = "robot.interface";

static atomic<bool> g_interface_running{false};
static atomic<bool> g_overlay_open{false};
static atomic<bool> g_listen_open{false};
static int g_listen_fd = -1;
static thread g_interface_thread;
static string g_socket_path;
static mutex g_clients_mutex;
static vector<int> g_client_fds;
static vector<thread> g_client_threads;
static steady_clock::time_point g_last_menu_tap{};

static mutex g_handoff_mutex;
static condition_variable g_handoff_cv;
static int g_handoff_command = CALL_HANDOFF_NONE;
static bool g_handoff_complete = false;
static bool g_handoff_ok = false;

static json position_reply();
static string handle_request(const string& line);
static void serve_client(int client_fd);
static void serve_client_thread(int client_fd);
static void interface_loop();
static string robot_interface_path();
static bool wait_call_handoff(int command);
static void add_client(int client_fd);
static void remove_client(int client_fd);
static void send_to_clients(const string& line);
static void send_menu();

int take_call_handoff() {
    lock_guard<mutex> lock(g_handoff_mutex);
    if (g_handoff_complete || g_handoff_command == CALL_HANDOFF_NONE) return CALL_HANDOFF_NONE;
    return g_handoff_command;
}

void complete_call_handoff(bool ok) {
    unique_lock<mutex> lock(g_handoff_mutex);
    g_handoff_ok = ok;
    g_handoff_complete = true;
    g_handoff_command = CALL_HANDOFF_NONE;
    g_handoff_cv.notify_all();
}

static bool wait_call_handoff(int command) {
    unique_lock<mutex> lock(g_handoff_mutex);
    g_handoff_command = command;
    g_handoff_complete = false;
    g_handoff_ok = false;
    if (!g_handoff_cv.wait_for(lock, seconds(CALL_HANDOFF_WAIT_SECONDS), [] { return g_handoff_complete; })) {
        g_handoff_command = CALL_HANDOFF_NONE;
        return false;
    }
    return g_handoff_ok;
}

static json position_reply() {
    int pan = 0;
    int tilt = 0;
    int hat = 0;
    get_percent(pan, tilt, hat);
    return {{"ok", true}, {"x", pan}, {"y", tilt}, {"hat", hat}};
}

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
        } else if (command == "pause") {
            if (!wait_call_handoff(CALL_HANDOFF_PAUSE)) reply = {{"ok", false}, {"error", "pause timeout"}};
            else reply = {{"ok", true}};
        } else if (command == "resume") {
            if (!wait_call_handoff(CALL_HANDOFF_RESUME)) reply = {{"ok", false}, {"error", "resume timeout"}};
            else reply = {{"ok", true}};
        } else if (command == "menu") {
            send_menu();
            reply = {{"ok", true}};
        } else if (command == "battery") {
            reply = {{"ok", true}, {"voltage", battery_voltage()}, {"percent", battery_percent()}, {"current", battery_current()}};
        } else if (command == "quit") {
            send_to_clients(json{{"command", "quit"}}.dump());
            g_quit = true;
            reply = {{"ok", true}};
        } else if (command == "listen") {
            g_listen_open = request.value("open", false);
            reply = {{"ok", true}};
        } else if (command == "overlay") {
            bool open = request.value("open", false);
            g_overlay_open = open;
            saveStatusOpen(open);
            reply = {{"ok", true}};
        } else {
            reply = {{"ok", false}, {"error", "unknown command"}};
        }
    } catch (const exception& error) {
        reply = {{"ok", false}, {"error", error.what()}};
    }
    return reply.dump();
}

static void add_client(int client_fd) {
    lock_guard<mutex> lock(g_clients_mutex);
    g_client_fds.push_back(client_fd);
}

static void remove_client(int client_fd) {
    lock_guard<mutex> lock(g_clients_mutex);
    g_client_fds.erase(remove(g_client_fds.begin(), g_client_fds.end(), client_fd), g_client_fds.end());
}

static void send_to_clients(const string& line) {
    string payload = line;
    if (payload.empty() || payload.back() != '\n') payload.push_back('\n');
    lock_guard<mutex> lock(g_clients_mutex);
    for (int client_fd : g_client_fds) {
        if (client_fd >= 0) write(client_fd, payload.data(), payload.size());
    }
}

static void send_menu() {
    auto now = steady_clock::now();
    if (g_last_menu_tap.time_since_epoch().count() != 0 && duration_cast<milliseconds>(now - g_last_menu_tap).count() < CALL_MENU_TAP_DEBOUNCE_MS) return;
    g_last_menu_tap = now;
    g_overlay_open = !g_overlay_open.load();
    saveStatusOpen(g_overlay_open.load());
    send_to_clients(json{{"command", "menu"}}.dump());
}

// True while Exit is showing on the bottom bar
bool call_overlay_open() {
    return g_overlay_open.load();
}

// Restore the status bar after a restart
void set_call_overlay_open(bool open) {
    g_overlay_open = open;
}

// True while talk is in the listening window
bool listen_open() {
    return g_listen_open.load();
}

void handle_call_event(const SDL_Event& event) {
    int x = 0;
    int y = 0;
    bool tap = false;
    if (event.type == SDL_MOUSEBUTTONDOWN && event.button.button == SDL_BUTTON_LEFT) {
        if (event.button.which == SDL_TOUCH_MOUSEID) return;
        tap = true;
        x = event.button.x;
        y = event.button.y;
    } else if (event.type == SDL_FINGERDOWN) {
        tap = true;
        x = (int)(event.tfinger.x * screen_width);
        y = (int)(event.tfinger.y * screen_height);
    }
    if (!tap) return;
    if (g_overlay_open.load() && tap_is_exit(x, y)) {
        send_to_clients(json{{"command", "quit"}}.dump());
        g_quit = true;
        return;
    }
    send_menu();
}

static void serve_client(int client_fd) {
    string buffer;
    char chunk[1024];
    while (g_interface_running.load()) {
        ssize_t n = read(client_fd, chunk, sizeof(chunk));
        if (n <= 0) break;
        buffer.append(chunk, static_cast<size_t>(n));

        size_t pos;
        while ((pos = buffer.find('\n')) != string::npos) {
            string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            if (line.empty()) continue;
            string reply = handle_request(line);
            reply.push_back('\n');
            if (write(client_fd, reply.data(), reply.size()) < 0) break;
        }
    }
    close(client_fd);
}

static void serve_client_thread(int client_fd) {
    add_client(client_fd);
    serve_client(client_fd);
    remove_client(client_fd);
}

static void interface_loop() {
    while (g_interface_running.load()) {
        int client_fd = ::accept(g_listen_fd, nullptr, nullptr);
        if (client_fd < 0) {
            if (!g_interface_running.load()) break;
            continue;
        }

        // One thread per client so look, pause, and talk can connect together
        thread client_thread(serve_client_thread, client_fd);
        lock_guard<mutex> lock(g_clients_mutex);
        g_client_threads.push_back(std::move(client_thread));
    }
}

static string robot_interface_path() {
    string directory;
    const char* runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime && runtime[0]) directory = runtime;
    else directory = string("/run/user/") + to_string(getuid());
    struct stat info;
    if (stat(directory.c_str(), &info) != 0 || !S_ISDIR(info.st_mode)) directory = "/tmp";
    return directory + "/" + ROBOT_INTERFACE_NAME;
}

bool start_interface() {
    if (g_interface_running.load()) return true;

    string socket_path = robot_interface_path();

    // Remove a leftover socket file from a previous run
    unlink(socket_path.c_str());

    // Create Unix domain socket
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (g_listen_fd < 0) {
        perror("interface socket");
        return false;
    }

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (socket_path.size() >= sizeof(addr.sun_path)) {
        cerr << "interface: socket path too long" << endl;
        close(g_listen_fd);
        g_listen_fd = -1;
        return false;
    }
    strncpy(addr.sun_path, socket_path.c_str(), sizeof(addr.sun_path) - 1);

    if (::bind(g_listen_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("interface bind");
        close(g_listen_fd);
        g_listen_fd = -1;
        return false;
    }

    chmod(socket_path.c_str(), 0600);

    if (::listen(g_listen_fd, 4) < 0) {
        perror("interface listen");
        close(g_listen_fd);
        g_listen_fd = -1;
        unlink(socket_path.c_str());
        return false;
    }

    g_socket_path = socket_path;
    g_interface_running = true;
    g_interface_thread = thread(interface_loop);
    cout << "Robot interface: " << socket_path << endl;
    return true;
}

void stop_interface() {
    if (!g_interface_running.load() && g_listen_fd < 0) return;

    g_interface_running = false;
    if (g_listen_fd >= 0) {
        shutdown(g_listen_fd, SHUT_RDWR);
        close(g_listen_fd);
        g_listen_fd = -1;
    }
    if (g_interface_thread.joinable()) g_interface_thread.join();

    vector<thread> client_threads;
    {
        lock_guard<mutex> lock(g_clients_mutex);
        for (int client_fd : g_client_fds) {
            if (client_fd >= 0) shutdown(client_fd, SHUT_RDWR);
        }
        client_threads.swap(g_client_threads);
    }
    for (thread& client_thread : client_threads) {
        if (client_thread.joinable()) client_thread.join();
    }
    g_client_fds.clear();

    if (!g_socket_path.empty()) {
        unlink(g_socket_path.c_str());
        g_socket_path.clear();
    }
}
