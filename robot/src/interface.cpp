// Unix socket for other programs to move the head, pause the camera, and open the menu.

// Local
#include "interface.h"
#include "servos.h"
#include "battery.h"
#include "screen.h"

// JSON
#include "json.hpp"

// System
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// Sockets
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

// Namespace
using json = nlohmann::json;
using namespace std;
using namespace std::chrono;

// Main quit flag, set by Ctrl-C and the Exit button
extern volatile bool g_quit;

// Give the main loop time to pause the camera, and swallow a double tap
const int CALL_HANDOFF_WAIT_SECONDS = 8;
const int CALL_MENU_TAP_DEBOUNCE_MS = 300;

// Turn on to print where each screen tap lands, off so the face status bar stays quiet
static const bool LOG_TAPS = false;

// Socket file name, backlog, owner-only mode, and the read buffer size
static const char* ROBOT_INTERFACE_NAME = "robot.interface";
static const int INTERFACE_BACKLOG = 4;
static const int INTERFACE_SOCKET_MODE = 0600;
static const int INTERFACE_CHUNK_SIZE = 1024;

// Listening socket, its thread, and the path to unlink on the way out
static atomic<bool> g_interface_running{false};
static int g_listen_fd = -1;
static thread g_interface_thread;
static string g_socket_path;

// Connected clients and the threads serving them
static mutex g_clients_mutex;
static vector<int> g_client_fds;
static vector<thread> g_client_threads;

// Overlay and listen state the face and tracker read
static atomic<bool> g_overlay_open{false};
static atomic<bool> g_listen_open{true};
static steady_clock::time_point g_last_menu_tap{};
static steady_clock::time_point g_interface_start{};

// Handoff of the camera between the robot and a call
static mutex g_handoff_mutex;
static condition_variable g_handoff_cv;
static int g_handoff_command = CALL_HANDOFF_NONE;
static bool g_handoff_complete = false;
static bool g_handoff_ok = false;

// Later in this file
static string robot_interface_path();
static void interface_loop();
static void serve_client_thread(int client_fd);
static void add_client(int client_fd);
static void serve_client(int client_fd);
static string handle_request(const string& line);
static json position_reply();
static int request_int(const json& request, const char* key, int fallback);
static bool wait_call_handoff(int command);
static void send_menu();
static void send_to_clients(const string& line);
static void remove_client(int client_fd);
static double seconds_since_start();

// Bind the socket and start accepting clients
bool start_interface() {
    if (g_interface_running.load())
        return true;

    // Stamp the start so tap logs can say how far into the boot they landed
    g_interface_start = steady_clock::now();

    // Remove a leftover socket file from a previous run
    string socket_path = robot_interface_path();
    unlink(socket_path.c_str());

    // Create Unix domain socket
    g_listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (g_listen_fd < 0) {
        perror("interface socket");
        return false;
    }

    // Copy the path into the address, it has to fit sun_path
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (socket_path.size() >= sizeof(addr.sun_path)) {
        cerr << "interface: socket path too long" << endl;
        close(g_listen_fd);
        g_listen_fd = -1;
        return false;
    }
    strncpy(addr.sun_path, socket_path.c_str(), sizeof(addr.sun_path) - 1);

    // Claim the path
    if (::bind(g_listen_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("interface bind");
        close(g_listen_fd);
        g_listen_fd = -1;
        return false;
    }

    // Keep the socket to this user
    chmod(socket_path.c_str(), INTERFACE_SOCKET_MODE);

    // Start listening, tidying the socket file if that fails
    if (::listen(g_listen_fd, INTERFACE_BACKLOG) < 0) {
        perror("interface listen");
        close(g_listen_fd);
        g_listen_fd = -1;
        unlink(socket_path.c_str());
        return false;
    }

    // Hand accepting off to its own thread
    g_socket_path = socket_path;
    g_interface_running = true;
    g_interface_thread = thread(interface_loop);
    cout << "Robot interface: " << socket_path << endl;
    return true;
}

// Put the socket in the runtime directory, falling back to /tmp
static string robot_interface_path() {
    // Prefer the session runtime directory
    string directory;
    const char* runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime && runtime[0])
        directory = runtime;
    else
        directory = string("/run/user/") + to_string(getuid());

    // Fall back to /tmp when that directory is missing
    struct stat info;
    if (stat(directory.c_str(), &info) != 0 || !S_ISDIR(info.st_mode))
        directory = "/tmp";
    return directory + "/" + ROBOT_INTERFACE_NAME;
}

// Accept clients until stop_interface shuts the socket down
static void interface_loop() {
    while (g_interface_running.load()) {
        // Wait for the next connection
        int client_fd = ::accept(g_listen_fd, nullptr, nullptr);
        if (client_fd < 0) {
            if (!g_interface_running.load())
                break;
            continue;
        }

        // One thread per client so look, pause, and talk can connect together
        thread client_thread(serve_client_thread, client_fd);
        lock_guard<mutex> lock(g_clients_mutex);
        g_client_threads.push_back(std::move(client_thread));
    }
}

// Track the client for broadcasts while it is being served
static void serve_client_thread(int client_fd) {
    add_client(client_fd);
    serve_client(client_fd);
    remove_client(client_fd);
}

// Add a client to the broadcast list
static void add_client(int client_fd) {
    lock_guard<mutex> lock(g_clients_mutex);
    g_client_fds.push_back(client_fd);
}

// Read newline separated JSON requests and write a reply to each
static void serve_client(int client_fd) {
    string buffer;
    char chunk[INTERFACE_CHUNK_SIZE];
    while (g_interface_running.load()) {
        // Take whatever arrived, an empty read means the client left
        ssize_t count = read(client_fd, chunk, sizeof(chunk));
        if (count <= 0)
            break;
        buffer.append(chunk, static_cast<size_t>(count));

        // Answer every whole line the buffer now holds
        size_t position;
        while ((position = buffer.find('\n')) != string::npos) {
            string line = buffer.substr(0, position);
            buffer.erase(0, position + 1);
            if (line.empty())
                continue;
            string reply = handle_request(line);
            reply.push_back('\n');
            if (write(client_fd, reply.data(), reply.size()) < 0)
                break;
        }
    }
    close(client_fd);
}

// Run one JSON command and return the JSON reply
static string handle_request(const string& line) {
    json reply;
    try {
        json request = json::parse(line);
        string command = request.value("command", "");

        // Report where the head is pointed
        if (command == "get") {
            reply = position_reply();

        // Start from the current pose in degrees, then apply absolute and delta keys
        } else if (command == "move") {
            int pan = 0;
            int tilt = 0;
            int hat = 0;
            get_degrees(pan, tilt, hat);
            if (request.contains("pan"))
                pan = request_int(request, "pan", pan);
            if (request.contains("tilt"))
                tilt = request_int(request, "tilt", tilt);
            if (request.contains("hat"))
                hat = request_int(request, "hat", hat);
            pan += request_int(request, "pan_delta", 0);
            tilt += request_int(request, "tilt_delta", 0);
            hat += request_int(request, "hat_delta", 0);
            set_degrees(pan, tilt, hat);
            reply = position_reply();

        // Hand the camera over to a call, then take it back
        } else if (command == "pause") {
            if (!wait_call_handoff(CALL_HANDOFF_PAUSE))
                reply = {{"ok", false}, {"error", "pause timeout"}};
            else
                reply = {{"ok", true}};
        } else if (command == "resume") {
            if (!wait_call_handoff(CALL_HANDOFF_RESUME))
                reply = {{"ok", false}, {"error", "resume timeout"}};
            else
                reply = {{"ok", true}};

        // Toggle the peer list
        } else if (command == "menu") {
            send_menu();
            reply = {{"ok", true}};

        // Report the last battery reading
        } else if (command == "battery") {
            reply = {{"ok", true}, {"voltage", battery_voltage()}, {"percent", battery_percent()}, {"current", battery_current()}};

        // Tell every client to quit, then quit
        } else if (command == "quit") {
            send_to_clients(json{{"command", "quit"}}.dump());
            g_quit = true;
            reply = {{"ok", true}};

        // Follow a face while talk is listening, and while an overlay is up
        } else if (command == "listen") {
            g_listen_open = request.value("open", false);
            reply = {{"ok", true}};
        } else if (command == "overlay") {
            g_overlay_open = request.value("open", false);
            reply = {{"ok", true}};
        } else {
            reply = {{"ok", false}, {"error", "unknown command"}};
        }

    // Hand the parse or command error back to the caller
    } catch (const exception& error) {
        reply = {{"ok", false}, {"error", error.what()}};
    }
    return reply.dump();
}

// Report where the head is pointed, in degrees
static json position_reply() {
    int pan = 0;
    int tilt = 0;
    int hat = 0;
    get_degrees(pan, tilt, hat);
    return {{"ok", true}, {"pan", pan}, {"tilt", tilt}, {"hat", hat}};
}

// Read a JSON number as int, or the fallback when the key is missing
static int request_int(const json& request, const char* key, int fallback) {
    if (!request.contains(key))
        return fallback;
    if (request[key].is_number_integer())
        return request[key].get<int>();
    if (request[key].is_number())
        return static_cast<int>(request[key].get<double>());
    return fallback;
}

// Post a pause or resume, then wait for the main loop to answer
static bool wait_call_handoff(int command) {
    unique_lock<mutex> lock(g_handoff_mutex);
    g_handoff_command = command;
    g_handoff_complete = false;
    g_handoff_ok = false;

    // Drop the request when the main loop never picks it up
    if (!g_handoff_cv.wait_for(lock, seconds(CALL_HANDOFF_WAIT_SECONDS), [] { return g_handoff_complete; })) {
        g_handoff_command = CALL_HANDOFF_NONE;
        return false;
    }
    return g_handoff_ok;
}

// Flip the peer list, ignoring a second tap that lands too soon
static void send_menu() {
    auto now = steady_clock::now();
    if (g_last_menu_tap.time_since_epoch().count() != 0 && duration_cast<milliseconds>(now - g_last_menu_tap).count() < CALL_MENU_TAP_DEBOUNCE_MS)
        return;
    g_last_menu_tap = now;
    g_overlay_open = !g_overlay_open.load();
    send_to_clients(json{{"command", "menu"}}.dump());
}

// Send one line to every connected client
static void send_to_clients(const string& line) {
    // Clients read by line, so make sure there is one
    string payload = line;
    if (payload.empty() || payload.back() != '\n')
        payload.push_back('\n');

    // Write to each client in turn, a dead one is cleaned up by its own thread
    lock_guard<mutex> lock(g_clients_mutex);
    for (int client_fd : g_client_fds) {
        if (client_fd < 0)
            continue;
        ssize_t written = write(client_fd, payload.data(), payload.size());
        (void)written;
    }
}

// Drop a client from the broadcast list
static void remove_client(int client_fd) {
    lock_guard<mutex> lock(g_clients_mutex);
    g_client_fds.erase(remove(g_client_fds.begin(), g_client_fds.end(), client_fd), g_client_fds.end());
}

// Return a pending pause or resume for the main loop to act on
int take_call_handoff() {
    lock_guard<mutex> lock(g_handoff_mutex);
    if (g_handoff_complete || g_handoff_command == CALL_HANDOFF_NONE)
        return CALL_HANDOFF_NONE;
    return g_handoff_command;
}

// Report the handoff result back to the waiting client
void complete_call_handoff(bool ok) {
    unique_lock<mutex> lock(g_handoff_mutex);
    g_handoff_ok = ok;
    g_handoff_complete = true;
    g_handoff_command = CALL_HANDOFF_NONE;
    g_handoff_cv.notify_all();
}

// Route a screen tap to Exit, to Call, or to the status bar
void handle_call_event(const SDL_Event& event) {
    // Take the tap position from a mouse click or a finger
    int x = 0;
    int y = 0;
    bool tap = false;
    const char* tap_source = "";
    if (event.type == SDL_MOUSEBUTTONDOWN && event.button.button == SDL_BUTTON_LEFT) {
        if (event.button.which == SDL_TOUCH_MOUSEID)
            return;
        tap = true;
        tap_source = "mouse";
        x = event.button.x;
        y = event.button.y;
    } else if (event.type == SDL_FINGERDOWN) {
        tap = true;
        tap_source = "finger";
        x = (int)(event.tfinger.x * screen_width);
        y = (int)(event.tfinger.y * screen_height);
    }
    if (!tap)
        return;

    // Overlay keeps the bar up so Exit and Call stay reachable
    bool bar_showing = status_bar_visible() || g_overlay_open.load();
    bool hit_exit = bar_showing && g_overlay_open.load() && tap_is_exit(x, y);
    bool hit_call = !hit_exit && bar_showing && tap_is_call(x, y);

    // Name what the tap landed on
    const char* hit_name = "face";
    if (hit_exit)
        hit_name = "Exit";
    if (hit_call)
        hit_name = "Call";

    // Log every tap so a stray one at boot stands apart from a real press
    if (LOG_TAPS) {
        printf("Tap %s at %d,%d of %dx%d hit %s, %.1f sec after start\n", tap_source, x, y, screen_width, screen_height, hit_name, seconds_since_start());
        fflush(stdout);
    }

    // Exit tells every client to quit, Call opens the peer list
    if (hit_exit) {
        send_to_clients(json{{"command", "quit"}}.dump());
        g_quit = true;
        return;
    }
    if (hit_call) {
        send_menu();
        return;
    }

    // A tap on the face shows or hides the status bar, unless the overlay owns it
    if (g_overlay_open.load())
        return;
    set_status_bar_visible(!bar_showing);
}

// Seconds since the interface started, so taps during boot are easy to spot
static double seconds_since_start() {
    if (g_interface_start.time_since_epoch().count() == 0)
        return 0.0;
    return duration_cast<milliseconds>(steady_clock::now() - g_interface_start).count() / 1000.0;
}

// True while the peer list is up and Exit is showing
bool call_overlay_open() {
    return g_overlay_open.load();
}

// True while face tracking should follow, off after ready following a listen
bool listen_open() {
    return g_listen_open.load();
}

// Close the socket, join every client thread, and remove the socket file
void stop_interface() {
    if (!g_interface_running.load() && g_listen_fd < 0)
        return;

    // Stop accepting, which wakes the interface thread
    g_interface_running = false;
    if (g_listen_fd >= 0) {
        shutdown(g_listen_fd, SHUT_RDWR);
        close(g_listen_fd);
        g_listen_fd = -1;
    }
    if (g_interface_thread.joinable())
        g_interface_thread.join();

    // Shut every client down, taking the thread list out from under the lock
    vector<thread> client_threads;
    {
        lock_guard<mutex> lock(g_clients_mutex);
        for (int client_fd : g_client_fds) {
            if (client_fd >= 0)
                shutdown(client_fd, SHUT_RDWR);
        }
        client_threads.swap(g_client_threads);
    }

    // Wait for the client threads to finish, then forget them
    for (thread& client_thread : client_threads) {
        if (client_thread.joinable())
            client_thread.join();
    }
    g_client_fds.clear();

    // Remove the socket file so the next run can bind
    if (!g_socket_path.empty()) {
        unlink(g_socket_path.c_str());
        g_socket_path.clear();
    }
}
