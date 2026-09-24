// Unix socket for other programs to move the head, pause the camera, and open the menu.

// Local
#include "interface.h"
#include "servos.h"
#include "battery.h"
#include "screen.h"
#include "recorder.h"
#include "player.h"
#include "wifi.h"

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
#include <system_error>
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

// A tap has no client waiting on a reply, so every client hears the broadcast
static const int NO_CLIENT = -1;

// Menu items stacked above the button
static const int MENU_QUIET = 0;
static const int MENU_LISTEN = 1;
static const int MENU_MOVE = 2;
static const int MENU_CAMERA = 3;
static const int MENU_RECORD = 4;
static const int MENU_VIDEOS = 5;
static const int MENU_MODE = 6;
static const int MENU_AUDIO = 7;
static const int MENU_WIFI = 8;
static const int MENU_CALL = 9;
static const int MENU_EXIT = 10;
static const char* MENU_ITEM_LABELS[] = {"Quiet", "Listen", "Move", "Camera", "Record", "Videos", "Mode", "Audio", "WiFi", "Call", "Exit"};

// Menu item size
static const int MENU_ITEM_COUNT = 11;

// The Record item says how to end the recording while one is running
static const char* MENU_STOP_LABEL = "Stop";
static const int MENU_ITEM_WIDTH = 160;

// Which model talk runs, the Mode button steps through these in order
static const char* TALK_MODE_LABELS[] = {"Local", "Cloud", "Realtime"};
static const int TALK_MODE_COUNT = 3;

// Wait out a run of taps before restarting talk, so stepping past a mode costs nothing
static const int TALK_MODE_APPLY_MS = 2000;

// Hamburger button size and its three lines
static const int HAMBURGER_BUTTON_WIDTH = 72;
static const int HAMBURGER_LINE_HEIGHT = 5;
static const int HAMBURGER_LINE_GAP = 7;

// The recordings list, rows of a file each with a delete button on the right
static const int VIDEO_LIST_TOP = 120;
static const int VIDEO_ROW_HEIGHT = 76;
static const int VIDEO_ROW_GAP = 8;
static const int VIDEO_LIST_PAD = 16;
static const int VIDEO_DELETE_WIDTH = 120;
static const int VIDEO_TEXT_PAD = 16;
static const char* VIDEO_DELETE_LABEL = "Delete";
static const char* VIDEO_CONFIRM_LABEL = "Sure?";
static const char* VIDEO_OLDER_LABEL = "Older";
static const char* VIDEO_NEWER_LABEL = "Newer";
static const char* VIDEO_EMPTY_TEXT = "No recordings yet";
static const char* VIDEO_PLAYING_TEXT = "Tap to stop";
static const char* VIDEO_MEASURING_TEXT = "--:--";

// The wireless list, a row for each network in range
static const char* WIFI_LOOKING_TEXT = "Looking...";
static const char* WIFI_SAVED_MARK = "saved";
static const char* WIFI_CONNECTED_MARK = "connected";

// Menu button colors
static const SDL_Color BUTTON_LABEL_COLOR = {255, 255, 255, 255};
static const SDL_Color EXIT_BUTTON_COLOR = {180, 40, 40, 255};
static const SDL_Color RECORDING_BUTTON_COLOR = {220, 30, 30, 255};
static const SDL_Color CAMERA_ON_COLOR = {30, 140, 70, 255};
static const SDL_Color VIDEO_ROW_COLOR = {60, 60, 70, 255};
static const SDL_Color VIDEO_DELETE_COLOR = {140, 50, 50, 255};
static const SDL_Color VIDEO_CONFIRM_COLOR = {220, 30, 30, 255};
static const SDL_Color VIDEO_BACKDROP_COLOR = {20, 20, 25, 235};
static const SDL_Color WIFI_ACTIVE_COLOR = {30, 140, 70, 255};
static const SDL_Color MENU_BUTTON_COLOR = {40, 90, 180, 255};
static const SDL_Color MENU_OPEN_COLOR = {30, 70, 150, 255};

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

// Overlay and listen state the face and tracker read
static atomic<bool> g_overlay_open{false};
static atomic<bool> g_listen_open{false};
static atomic<bool> g_camera_toggle{false};
static atomic<bool> g_camera_showing{false};
static atomic<bool> g_move_request{false};
static atomic<bool> g_audio_request{false};
static atomic<int> g_record_request{RECORD_REQUEST_NONE};
static atomic<bool> g_play_request{false};

// The recordings list, where the files are and which row asked to be deleted
static string g_recordings_path;
static atomic<bool> g_video_list_open{false};
static vector<Recording> g_recordings;
static int g_video_first_row = 0;
static string g_video_confirm_path;

// The wireless list
static atomic<bool> g_wifi_list_open{false};
static atomic<bool> g_menu_open{false};
static steady_clock::time_point g_last_menu_tap{};

// Mode shown on the button, and the tap that has not reached talk yet
static atomic<int> g_talk_mode{0};
static atomic<bool> g_talk_mode_pending{false};
static steady_clock::time_point g_talk_mode_tap{};
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
static string handle_request(const string& line, int from_fd);
static json position_reply();
static int request_int(const json& request, const char* key, int fallback);
static bool wait_call_handoff(int command);
static void send_menu();
static bool debounce_tap();
static void set_menu_open(bool open);
static const char* menu_item_label(int index);
static SDL_Color menu_item_color(int index);
static SDL_Rect menu_button_rect();
static SDL_Rect menu_item_rect(int index);
static void open_video_list();
static bool handle_video_tap(int x, int y);
static SDL_Rect video_row_rect(int row);
static SDL_Rect video_delete_rect(int row);
static SDL_Rect video_page_rect(bool older);
static int video_rows_that_fit();
static bool handle_wifi_tap(int x, int y);
static void draw_list_backdrop();
static bool tap_in_rect(int x, int y, SDL_Rect rect);
static void draw_bar_button(SDL_Rect rect, const char* label, SDL_Color fill, TTF_Font* font);
static void draw_hamburger_icon(SDL_Rect rect);
static void send_to_clients(const string& line, int skip_fd);
static void remove_client(int client_fd);
static double seconds_since_start();

// Bind the socket and start accepting clients
bool start_interface(const string& recordings_path) {
    if (g_interface_running.load())
        return true;

    // Remember where the recordings are, the Videos list reads that folder
    g_recordings_path = recordings_path;

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

        // One thread per request, detach so finished ones do not pile up
        try {
            thread client_thread(serve_client_thread, client_fd);
            client_thread.detach();
        } catch (const system_error& error) {
            printf("Interface client thread failed: %s\n", error.what());
            fflush(stdout);
            close(client_fd);
        }
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
            string reply = handle_request(line, client_fd);
            reply.push_back('\n');
            if (write(client_fd, reply.data(), reply.size()) < 0)
                break;
        }
    }
    close(client_fd);
}

// Run one JSON command and return the JSON reply, the asking client hears no broadcast
static string handle_request(const string& line, int from_fd) {
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

        // Start or stop a recording, the main loop does the work as the camera moves hands
        } else if (command == "record") {
            bool start = request.value("start", true);
            if (start && recording()) {
                reply = {{"ok", false}, {"error", "already recording"}};
            } else if (!start && !recording()) {
                reply = {{"ok", false}, {"error", "not recording"}};
            } else {
                g_record_request = start ? RECORD_REQUEST_START : RECORD_REQUEST_STOP;
                reply = {{"ok", true}};
            }

        // Play the newest recording on the screen
        } else if (command == "play") {
            if (g_recordings_path.empty() || list_recordings(g_recordings_path).empty()) {
                reply = {{"ok", false}, {"error", "no recordings"}};
            } else {
                g_play_request = true;
                reply = {{"ok", true}};
            }

        // Toggle the peer list
        } else if (command == "menu") {
            send_menu();
            reply = {{"ok", true}};

        // Report the last battery reading, or say the meter is missing
        } else if (command == "battery") {
            if (battery_voltage() <= 0)
                reply = {{"ok", false}, {"error", "no battery meter"}};
            else
                reply = {{"ok", true}, {"voltage", battery_voltage()}, {"percent", battery_percent()}, {"current", battery_current()}};

        // Tell every client to quit, then quit
        } else if (command == "quit") {
            send_to_clients(json{{"command", "quit"}}.dump(), from_fd);
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
    if (!debounce_tap())
        return;
    g_overlay_open = !g_overlay_open.load();
    send_to_clients(json{{"command", "menu"}}.dump(), NO_CLIENT);
}

// True when this tap is far enough from the last one
static bool debounce_tap() {
    auto now = steady_clock::now();
    if (g_last_menu_tap.time_since_epoch().count() != 0 && duration_cast<milliseconds>(now - g_last_menu_tap).count() < CALL_MENU_TAP_DEBOUNCE_MS)
        return false;
    g_last_menu_tap = now;
    return true;
}

// Send one line to every connected client, apart from one waiting for a reply
static void send_to_clients(const string& line, int skip_fd) {
    // Clients read by line, so make sure there is one
    string payload = line;
    if (payload.empty() || payload.back() != '\n')
        payload.push_back('\n');

    // Write to each client in turn, a dead one is cleaned up by its own thread
    lock_guard<mutex> lock(g_clients_mutex);
    for (int client_fd : g_client_fds) {
        if (client_fd < 0 || client_fd == skip_fd)
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

// Name one popup item, Mode wears the model talk is set to and Record turns into Stop
static const char* menu_item_label(int index) {
    if (index == MENU_MODE)
        return TALK_MODE_LABELS[g_talk_mode.load()];
    if (index == MENU_RECORD && recording())
        return MENU_STOP_LABEL;
    return MENU_ITEM_LABELS[index];
}

// Colour one popup item, Exit is red, Stop goes brighter red, and Camera goes green while the preview is up
static SDL_Color menu_item_color(int index) {
    if (index == MENU_RECORD && recording())
        return RECORDING_BUTTON_COLOR;
    if (index == MENU_CAMERA && g_camera_showing.load())
        return CAMERA_ON_COLOR;
    if (index == MENU_EXIT)
        return EXIT_BUTTON_COLOR;
    return MENU_BUTTON_COLOR;
}

// Place the menu toggle on the right of the status bar
static SDL_Rect menu_button_rect() {
    int pad = status_bar_pad();
    int bar_y = screen_height - status_bar_height();
    int height = status_bar_height() - pad * 2;
    return {screen_width - pad - HAMBURGER_BUTTON_WIDTH, bar_y + pad, HAMBURGER_BUTTON_WIDTH, height};
}

// Place a popup item above the menu button, one extra gap under Exit, Quiet at the top
static SDL_Rect menu_item_rect(int index) {
    SDL_Rect menu_rect = menu_button_rect();
    int pad = status_bar_pad();
    int from_bottom = MENU_ITEM_COUNT - index;
    return {screen_width - pad - MENU_ITEM_WIDTH, menu_rect.y - from_bottom * (menu_rect.h + pad) - pad, MENU_ITEM_WIDTH, menu_rect.h};
}

// True when a tap lands inside a button
static bool tap_in_rect(int x, int y, SDL_Rect rect) {
    return x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h;
}

// Draw a filled button with a centered label
static void draw_bar_button(SDL_Rect rect, const char* label, SDL_Color fill, TTF_Font* font) {
    SDL_SetRenderDrawColor(renderer, fill.r, fill.g, fill.b, fill.a);
    SDL_RenderFillRect(renderer, &rect);
    if (!font || !label)
        return;

    // Measure the label so it can sit in the middle of the button
    int text_width = 0;
    int text_height = 0;
    if (TTF_SizeUTF8(font, label, &text_width, &text_height) != 0)
        return;
    draw_text(label, rect.x + (rect.w - text_width) / 2, rect.y + (rect.h - text_height) / 2, font, BUTTON_LABEL_COLOR);
}

// Draw three horizontal lines in the menu button
static void draw_hamburger_icon(SDL_Rect rect) {
    int line_width = rect.w / 2;
    int total_height = HAMBURGER_LINE_HEIGHT * 3 + HAMBURGER_LINE_GAP * 2;
    int x = rect.x + (rect.w - line_width) / 2;
    int y = rect.y + (rect.h - total_height) / 2;
    SDL_SetRenderDrawColor(renderer, BUTTON_LABEL_COLOR.r, BUTTON_LABEL_COLOR.g, BUTTON_LABEL_COLOR.b, BUTTON_LABEL_COLOR.a);
    for (int line = 0; line < 3; line++) {
        SDL_Rect bar = {x, y + line * (HAMBURGER_LINE_HEIGHT + HAMBURGER_LINE_GAP), line_width, HAMBURGER_LINE_HEIGHT};
        SDL_RenderFillRect(renderer, &bar);
    }
}

// Draw the menu button, and the popup above it when it is open
void draw_menu(TTF_Font* font) {
    if (g_menu_open) {
        for (int index = 0; index < MENU_ITEM_COUNT; index++) {
            draw_bar_button(menu_item_rect(index), menu_item_label(index), menu_item_color(index), font);
        }
    }

    // Three-line toggle on the right of the status bar
    SDL_Rect menu_rect = menu_button_rect();
    SDL_Color menu_fill = g_menu_open ? MENU_OPEN_COLOR : MENU_BUTTON_COLOR;
    draw_bar_button(menu_rect, nullptr, menu_fill, font);
    draw_hamburger_icon(menu_rect);
}

// Draw the recordings list, or the video that is playing
void draw_video_list(TTF_Font* font) {
    // The video fills the space the list was using, with a hint on how to stop it
    if (playing()) {
        draw_playback_frame();
        draw_text(VIDEO_PLAYING_TEXT, VIDEO_LIST_PAD, VIDEO_LIST_TOP, font, BUTTON_LABEL_COLOR);
        return;
    }
    if (!g_video_list_open.load())
        return;

    // Darken the face behind the list
    draw_list_backdrop();

    // Pick up the lengths once they have been measured
    if (recording_lengths_updated())
        g_recordings = list_recordings(g_recordings_path);

    // Say so when nothing has been recorded
    if (g_recordings.empty()) {
        draw_text(VIDEO_EMPTY_TEXT, VIDEO_LIST_PAD, VIDEO_LIST_TOP, font, BUTTON_LABEL_COLOR);
        return;
    }

    // A row for each recording on this page, date and length and size, delete on the right
    int rows = video_rows_that_fit();
    for (int row = 0; row < rows; row++) {
        int index = g_video_first_row + row;
        if (index >= (int)g_recordings.size())
            break;
        const Recording& recording = g_recordings[index];
        SDL_Rect rowRect = video_row_rect(row);
        draw_bar_button(rowRect, nullptr, VIDEO_ROW_COLOR, font);
        string length = recording.seconds > 0 ? length_text(recording.seconds) : VIDEO_MEASURING_TEXT;
        string text = recording.dateText + "   " + length + "   " + size_text(recording.bytes);
        int textHeight = 0;
        TTF_SizeUTF8(font, text.c_str(), nullptr, &textHeight);
        draw_text(text.c_str(), rowRect.x + VIDEO_TEXT_PAD, rowRect.y + (rowRect.h - textHeight) / 2, font, BUTTON_LABEL_COLOR);

        // The delete button asks once before it removes anything
        bool confirming = recording.path == g_video_confirm_path;
        const char* deleteLabel = confirming ? VIDEO_CONFIRM_LABEL : VIDEO_DELETE_LABEL;
        SDL_Color deleteColor = confirming ? VIDEO_CONFIRM_COLOR : VIDEO_DELETE_COLOR;
        draw_bar_button(video_delete_rect(row), deleteLabel, deleteColor, font);
    }

    // Page buttons, only when there is somewhere to page to
    if (g_video_first_row > 0)
        draw_bar_button(video_page_rect(false), VIDEO_NEWER_LABEL, MENU_BUTTON_COLOR, font);
    if (g_video_first_row + rows < (int)g_recordings.size())
        draw_bar_button(video_page_rect(true), VIDEO_OLDER_LABEL, MENU_BUTTON_COLOR, font);
}

// Read the folder again and show the list
static void open_video_list() {
    g_recordings = list_recordings(g_recordings_path);
    g_video_first_row = 0;
    g_video_confirm_path.clear();
    g_video_list_open = true;
}

// Play, delete, or page, and say whether the tap belonged to the list
static bool handle_video_tap(int x, int y) {
    // A tap anywhere stops the video that is playing
    if (playing()) {
        stop_playback();
        return true;
    }
    if (!g_video_list_open.load())
        return false;

    // Page through the recordings
    int rows = video_rows_that_fit();
    if (g_video_first_row > 0 && tap_in_rect(x, y, video_page_rect(false))) {
        g_video_first_row = max(0, g_video_first_row - rows);
        g_video_confirm_path.clear();
        return true;
    }
    if (g_video_first_row + rows < (int)g_recordings.size() && tap_in_rect(x, y, video_page_rect(true))) {
        g_video_first_row += rows;
        g_video_confirm_path.clear();
        return true;
    }

    // Play the row that was tapped, or work the delete button on it
    for (int row = 0; row < rows; row++) {
        int index = g_video_first_row + row;
        if (index >= (int)g_recordings.size())
            break;
        string path = g_recordings[index].path;
        if (tap_in_rect(x, y, video_delete_rect(row))) {
            // Ask first, then delete on the second tap
            if (g_video_confirm_path != path) {
                g_video_confirm_path = path;
                return true;
            }
            delete_recording(path);
            open_video_list();
            return true;
        }
        if (tap_in_rect(x, y, video_row_rect(row))) {
            g_video_confirm_path.clear();
            start_playback(path);
            g_video_list_open = false;
            return true;
        }
    }

    // A tap anywhere else closes the list
    g_video_list_open = false;
    g_video_confirm_path.clear();
    return true;
}

// Place one row of the list
static SDL_Rect video_row_rect(int row) {
    int y = VIDEO_LIST_TOP + row * (VIDEO_ROW_HEIGHT + VIDEO_ROW_GAP);
    return {VIDEO_LIST_PAD, y, screen_width - VIDEO_LIST_PAD * 2, VIDEO_ROW_HEIGHT};
}

// Place the delete button inside a row
static SDL_Rect video_delete_rect(int row) {
    SDL_Rect rowRect = video_row_rect(row);
    return {rowRect.x + rowRect.w - VIDEO_DELETE_WIDTH, rowRect.y, VIDEO_DELETE_WIDTH, rowRect.h};
}

// Place a page button under the rows, older on the right and newer on the left
static SDL_Rect video_page_rect(bool older) {
    int y = VIDEO_LIST_TOP + video_rows_that_fit() * (VIDEO_ROW_HEIGHT + VIDEO_ROW_GAP);
    int width = MENU_ITEM_WIDTH;
    int x = older ? screen_width - VIDEO_LIST_PAD - width : VIDEO_LIST_PAD;
    return {x, y, width, VIDEO_ROW_HEIGHT};
}

// How many rows there is room for above the status bar, leaving space for the page buttons
static int video_rows_that_fit() {
    int room = screen_height - status_bar_height() - VIDEO_LIST_TOP - VIDEO_ROW_HEIGHT - VIDEO_ROW_GAP;
    return max(1, room / (VIDEO_ROW_HEIGHT + VIDEO_ROW_GAP));
}

// True while the recordings list or a video is up
bool video_list_open() {
    return g_video_list_open.load() || playing();
}

// Draw the wireless networks, the one joined at the top
void draw_wifi_list(TTF_Font* font) {
    if (!g_wifi_list_open.load())
        return;

    // Darken the face behind the list
    draw_list_backdrop();

    // Where the robot is connected, and on what address
    string header = wifi_busy() ? WIFI_LOOKING_TEXT : wifi_status_text();
    draw_text(header.c_str(), VIDEO_LIST_PAD + VIDEO_TEXT_PAD, VIDEO_LIST_TOP, font, BUTTON_LABEL_COLOR);

    // A row for each network in range, name, strength, and whether it can be joined
    vector<Network> networks = wifi_networks();
    int rows = video_rows_that_fit() - 1;
    for (int row = 0; row < rows && row < (int)networks.size(); row++) {
        const Network& network = networks[row];
        SDL_Rect rowRect = video_row_rect(row + 1);
        draw_bar_button(rowRect, nullptr, network.active ? WIFI_ACTIVE_COLOR : VIDEO_ROW_COLOR, font);
        string mark = network.active ? WIFI_CONNECTED_MARK : (network.saved ? WIFI_SAVED_MARK : "");
        string text = network.name + "   " + to_string(network.signal) + "%   " + mark;
        int textHeight = 0;
        TTF_SizeUTF8(font, text.c_str(), nullptr, &textHeight);
        draw_text(text.c_str(), rowRect.x + VIDEO_TEXT_PAD, rowRect.y + (rowRect.h - textHeight) / 2, font, BUTTON_LABEL_COLOR);
    }
}

// Join the network that was tapped, and say whether the tap belonged to the list
static bool handle_wifi_tap(int x, int y) {
    if (!g_wifi_list_open.load())
        return false;

    // Tapping a network joins it, one without a saved password will not take
    vector<Network> networks = wifi_networks();
    int rows = video_rows_that_fit() - 1;
    for (int row = 0; row < rows && row < (int)networks.size(); row++) {
        if (!tap_in_rect(x, y, video_row_rect(row + 1)))
            continue;
        if (!networks[row].active)
            connect_network(networks[row].name);
        return true;
    }

    // A tap anywhere else closes the list
    g_wifi_list_open = false;
    return true;
}

// True while the wireless list is up
bool wifi_list_open() {
    return g_wifi_list_open.load();
}

// Dim the face so a list on top of it reads
static void draw_list_backdrop() {
    SDL_Rect backdrop = {0, 0, screen_width, screen_height};
    SDL_SetRenderDrawBlendMode(renderer, SDL_BLENDMODE_BLEND);
    SDL_SetRenderDrawColor(renderer, VIDEO_BACKDROP_COLOR.r, VIDEO_BACKDROP_COLOR.g, VIDEO_BACKDROP_COLOR.b, VIDEO_BACKDROP_COLOR.a);
    SDL_RenderFillRect(renderer, &backdrop);
}

// True when the popup list is showing
bool menu_open() {
    return g_menu_open;
}

// Left edge of the menu button, so status text stops before it
int menu_button_left() {
    return menu_button_rect().x;
}

// Show or hide the popup list
static void set_menu_open(bool open) {
    g_menu_open = open;
    if (open)
        set_status_bar_visible(true);
}

// Route a screen tap to the menu popup, or to the status bar
void handle_call_event(const SDL_Event& event) {
    // Take the tap from the mouse event, touch arrives as one with the xrandr mapping
    int x = 0;
    int y = 0;
    bool tap = false;
    const char* tap_source = "";
    if (event.type == SDL_MOUSEBUTTONDOWN && event.button.button == SDL_BUTTON_LEFT) {
        tap = true;
        tap_source = event.button.which == SDL_TOUCH_MOUSEID ? "touch" : "mouse";
        x = event.button.x;
        y = event.button.y;
    } else if (event.type == SDL_FINGERDOWN) {
        // Same press as the mouse event above, skip so one tap does not fire twice
        return;
    }
    if (!tap)
        return;

    // The lists and a playing video take the tap before anything else
    if (!menu_open() && handle_video_tap(x, y))
        return;
    if (!menu_open() && handle_wifi_tap(x, y))
        return;

    // Keep the bar reachable while the popup or a call overlay is up
    bool bar_showing = status_bar_visible() || menu_open() || g_overlay_open.load();
    int item = -1;
    if (bar_showing && menu_open()) {
        for (int index = 0; index < MENU_ITEM_COUNT; index++) {
            if (tap_in_rect(x, y, menu_item_rect(index))) {
                item = index;
                break;
            }
        }
    }
    bool hit_menu = bar_showing && item < 0 && tap_in_rect(x, y, menu_button_rect());

    // Name what the tap landed on
    const char* hit_name = "face";
    if (item >= 0)
        hit_name = menu_item_label(item);
    else if (hit_menu)
        hit_name = "Menu";

    // Log every tap so a stray one at boot stands apart from a real press
    if (LOG_TAPS) {
        printf("Tap %s at %d,%d of %dx%d hit %s, %.1f sec after start\n", tap_source, x, y, screen_width, screen_height, hit_name, seconds_since_start());
        fflush(stdout);
    }

    // Popup items close the list, then do the action, Mode stays up so it can be stepped again
    if (item >= 0 && item != MENU_MODE)
        set_menu_open(false);
    if (item == MENU_EXIT) {
        send_to_clients(json{{"command", "quit"}}.dump(), NO_CLIENT);
        g_quit = true;
        return;
    }
    if (item == MENU_QUIET) {
        send_quiet();
        return;
    }
    if (item == MENU_LISTEN) {
        send_to_clients(json{{"command", "wake"}}.dump(), NO_CLIENT);
        return;
    }
    if (item == MENU_MOVE) {
        g_move_request = true;
        return;
    }
    if (item == MENU_CAMERA) {
        g_camera_toggle = true;
        return;
    }
    if (item == MENU_AUDIO) {
        g_audio_request = true;
        return;
    }
    if (item == MENU_RECORD) {
        g_record_request = RECORD_REQUEST_TOGGLE;
        return;
    }
    if (item == MENU_VIDEOS) {
        open_video_list();
        return;
    }
    if (item == MENU_WIFI) {
        refresh_networks();
        g_wifi_list_open = true;
        return;
    }
    if (item == MENU_MODE) {
        g_talk_mode = (g_talk_mode.load() + 1) % TALK_MODE_COUNT;
        g_talk_mode_pending = true;
        g_talk_mode_tap = steady_clock::now();
        return;
    }
    if (item == MENU_CALL) {
        g_overlay_open = !g_overlay_open.load();
        send_to_clients(json{{"command", "menu"}}.dump(), NO_CLIENT);
        return;
    }

    // Menu button toggles the popup above it
    if (hit_menu) {
        if (!debounce_tap())
            return;
        set_menu_open(!menu_open());
        return;
    }

    // A tap elsewhere closes the popup, or shows and hides the status bar
    if (menu_open()) {
        set_menu_open(false);
        return;
    }
    if (g_overlay_open.load())
        return;
    set_status_bar_visible(!bar_showing);
}

// Tell talk to stop what it is doing and go back to ready
void send_quiet() {
    send_to_clients(json{{"command", "quiet"}}.dump(), NO_CLIENT);
}

// Seconds since the interface started, so taps during boot are easy to spot
static double seconds_since_start() {
    if (g_interface_start.time_since_epoch().count() == 0)
        return 0.0;
    return duration_cast<milliseconds>(steady_clock::now() - g_interface_start).count() / 1000.0;
}

// True while the peer list is up
bool call_overlay_open() {
    return g_overlay_open.load();
}

// Camera, Move, and Audio presses since the last check, then clear them
void take_menu_presses(bool& camera, bool& move, bool& audio) {
    camera = g_camera_toggle.load();
    g_camera_toggle = false;
    move = g_move_request.load();
    g_move_request = false;
    audio = g_audio_request.load();
    g_audio_request = false;
}

// What the Record button or a voice command asked for, then clear it
int take_record_request() {
    return g_record_request.exchange(RECORD_REQUEST_NONE);
}

// True when something asked to play the newest recording, then clear it
bool take_play_request() {
    return g_play_request.exchange(false);
}

// True while face tracking should follow, off in ready until talk hears the wake word
bool listen_open() {
    return g_listen_open.load();
}

// Show whether the preview is up, so the Camera button can say so
void set_camera_showing(bool showing) {
    g_camera_showing = showing;
}

// Show the mode talk is running, so the button starts on the right label
void set_talk_mode(int mode) {
    g_talk_mode = mode;
}

// The mode the button settled on, or no mode while taps are still coming
int take_talk_mode_request() {
    if (!g_talk_mode_pending.load())
        return TALK_MODE_NONE;
    if (duration_cast<milliseconds>(steady_clock::now() - g_talk_mode_tap).count() < TALK_MODE_APPLY_MS)
        return TALK_MODE_NONE;
    g_talk_mode_pending = false;
    return g_talk_mode.load();
}

// Name one talk mode for the log
const char* talk_mode_name(int mode) {
    return TALK_MODE_LABELS[mode];
}

// Close the socket, wake detached clients, and remove the socket file
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

    // Shut every client down so its detached thread can leave
    {
        lock_guard<mutex> lock(g_clients_mutex);
        for (int client_fd : g_client_fds) {
            if (client_fd >= 0)
                shutdown(client_fd, SHUT_RDWR);
        }
        g_client_fds.clear();
    }

    // Remove the socket file so the next run can bind
    if (!g_socket_path.empty()) {
        unlink(g_socket_path.c_str());
        g_socket_path.clear();
    }
}
