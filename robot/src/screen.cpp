#include "screen.h"
#include <SDL2/SDL_ttf.h>
#include "face.h"
#include <string>
#include <mutex>
#include <iostream>
#include <cstdlib>
#include <unistd.h>
#include <fcntl.h>
#include <thread>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <system_error>

// Screen globals
SDL_Window* window = nullptr;
SDL_Renderer* renderer = nullptr;
int screen_width = 800;
int screen_height = 600;

static const char* DEFAULT_DISPLAY = ":0";

// Bottom strip, battery on the left and Exit on the right
static const int BOTTOM_BAR_HEIGHT = 96;
static const int BOTTOM_BAR_PAD = 12;
static const int BOTTOM_BAR_LINE_GAP = 4;
static const int EXIT_BUTTON_WIDTH = 100;
static const int LOG_TAIL_BYTES = 8192;
static const int LOG_LINE_MAX = 160;
static const char* LOG_FILE_NAME = "log.txt";
static const char* MONTH_NAMES[] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
};
static const char* DAY_NAMES[] = {
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
};

// A dead IBus socket, GNOME pops its touch keyboard when SDL takes input method focus
static const char* DEAD_IBUS_ADDRESS = "unix:path=/nonexistent";

// Grey bar and Call start visible, a tap on the face hides them
static bool g_status_bar_visible = true;

// Status text
string currentStatus;
mutex statusMutex;

// Set status text
void setStatus(const string& status) {
    unique_lock<mutex> lock(statusMutex);
    if (currentStatus.find("Sleeping") == string::npos || status.find("Sleeping") == string::npos) 
        if (status.find("Listening") == string::npos)
            if (!status.empty())
                cout << status.c_str() << endl;
    currentStatus = status;
}

// Tail of robot/log.txt for the on-screen line
static string g_last_log_line;
static string g_log_pending;
static uintmax_t g_log_offset = 0;
static bool g_log_opened = false;

// robot/build/robot sits under robot/, log.txt is next to that folder
static string robot_log_path() {
    error_code error;
    filesystem::path executable = filesystem::read_symlink("/proc/self/exe", error);
    if (error) return string("../") + LOG_FILE_NAME;
    return (executable.parent_path().parent_path() / LOG_FILE_NAME).string();
}

static int g_log_console_fd = -1;
static int g_log_file_fd = -1;
static int g_log_pipe_read = -1;

// Copy each chunk to the console and to log.txt
static void write_robot_log_loop() {
    char buffer[4096];
    while (true) {
        ssize_t count = read(g_log_pipe_read, buffer, sizeof(buffer));
        if (count <= 0) break;
        if (g_log_console_fd >= 0) write(g_log_console_fd, buffer, count);
        if (g_log_file_fd >= 0) write(g_log_file_fd, buffer, count);
    }
}

// Append stdout and stderr to robot/log.txt, and still print to the console
void start_robot_log() {
    string path = robot_log_path();

    // Keep the original console so lines still print when run from a terminal
    int console_fd = dup(STDOUT_FILENO);
    if (console_fd < 0) return;

    // Create or append robot/log.txt
    int log_fd = open(path.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (log_fd < 0) {
        close(console_fd);
        return;
    }

    int pipe_fds[2];
    if (pipe(pipe_fds) != 0) {
        close(console_fd);
        close(log_fd);
        return;
    }

    g_log_console_fd = console_fd;
    g_log_file_fd = log_fd;
    g_log_pipe_read = pipe_fds[0];
    thread(write_robot_log_loop).detach();

    // Point stdout and stderr at the tee pipe so talk inherits it too
    dup2(pipe_fds[1], STDOUT_FILENO);
    dup2(pipe_fds[1], STDERR_FILENO);
    if (pipe_fds[1] != STDOUT_FILENO && pipe_fds[1] != STDERR_FILENO) {
        close(pipe_fds[1]);
    }

    // Flush each line so the face log tail and the file stay current
    setvbuf(stdout, nullptr, _IOLBF, 0);
    setvbuf(stderr, nullptr, _IOLBF, 0);

    // Blank line and start stamp, same as the old service log
    time_t now = time(nullptr);
    tm local_time{};
    localtime_r(&now, &local_time);
    int hour = local_time.tm_hour;
    const char* suffix = hour >= 12 ? "pm" : "am";
    int hour12 = hour % 12;
    if (hour12 == 0) hour12 = 12;
    char stamp[64];
    snprintf(stamp, sizeof(stamp), "%s %s %d %d, %d:%02d%s", DAY_NAMES[local_time.tm_wday], MONTH_NAMES[local_time.tm_mon], local_time.tm_mday, local_time.tm_year + 1900, hour12, local_time.tm_min, suffix);
    cout << endl;
    cout << "=== Robot on " << stamp << " ===" << endl;
}

// Newest complete line from robot/log.txt, talk and robot both write there
string last_log_line() {
    error_code error;
    string path = robot_log_path();
    uintmax_t size = filesystem::file_size(path, error);
    if (error) return g_last_log_line;

    // File was truncated, read from the start
    if (size < g_log_offset) {
        g_log_offset = 0;
        g_log_pending.clear();
        g_log_opened = false;
    }

    // First open, skip the old history and keep the tail
    if (!g_log_opened) {
        g_log_opened = true;
        if (size > static_cast<uintmax_t>(LOG_TAIL_BYTES)) g_log_offset = size - LOG_TAIL_BYTES;
    }
    if (size == g_log_offset) return g_last_log_line;

    ifstream input(path);
    if (!input) return g_last_log_line;
    input.seekg(static_cast<streamoff>(g_log_offset));
    string chunk((istreambuf_iterator<char>(input)), istreambuf_iterator<char>());
    g_log_offset = size;
    for (char& character : chunk) {
        if (character == '\r') character = '\n';
    }
    g_log_pending += chunk;

    // Keep the last non-empty line
    size_t newline;
    while ((newline = g_log_pending.find('\n')) != string::npos) {
        string line = g_log_pending.substr(0, newline);
        g_log_pending.erase(0, newline + 1);
        while (!line.empty() && (line.back() == ' ' || line.back() == '\t')) line.pop_back();
        if (line.empty()) continue;
        if (static_cast<int>(line.size()) > LOG_LINE_MAX) line.resize(LOG_LINE_MAX);
        g_last_log_line = line;
    }
    return g_last_log_line;
}

// Create window
bool create_window() {
    bool fullscreen = false;
    #ifdef __linux__
    fullscreen = true; 
    #endif

    // Default DISPLAY to the local HDMI seat when unset
    if (getenv("DISPLAY") == nullptr || getenv("DISPLAY")[0] == '\0') {
        setenv("DISPLAY", DEFAULT_DISPLAY, 1);
    }

    // Point at the gdm Xauthority file when present
    if (getenv("XAUTHORITY") == nullptr || getenv("XAUTHORITY")[0] == '\0') {
        string xauth = "/run/user/" + to_string(getuid()) + "/gdm/Xauthority";
        if (filesystem::exists(xauth)) setenv("XAUTHORITY", xauth.c_str(), 1);
    }

    // Keep the on-screen keyboard away, SDL connects to IBus while it initializes
    setenv("IBUS_ADDRESS", DEAD_IBUS_ADDRESS, 1);

    // Initialize SDL
    if (SDL_Init(SDL_INIT_VIDEO) < 0) {
        fprintf(stderr, "Error: No available screen\n");
        return false;
    }

    // SDL turns text input on during init, we take no typing
    SDL_StopTextInput();

    // Initialize SDL_image
    if (IMG_Init(IMG_INIT_JPG | IMG_INIT_PNG | IMG_INIT_TIF) == 0) {
        fprintf(stderr, "SDL_image could not initialize: %s\n", SDL_GetError());
        SDL_Quit();
        return false;
    }

    // Initialize SDL_ttf
    if (TTF_Init() < 0) {
        fprintf(stderr, "SDL_ttf could not initialize: %s\n", TTF_GetError());
        SDL_Quit();
        return false;
    }

    // Get screen size
    SDL_DisplayMode display_mode;
    if (SDL_GetCurrentDisplayMode(0, &display_mode) != 0) {
        fprintf(stderr, "No display mode available: %s\n", SDL_GetError());
        IMG_Quit();
        SDL_Quit();
        return false;
    }
    screen_width = display_mode.w;
    screen_height = display_mode.h;

    // Cover the current desktop, exclusive fullscreen can lock the pre-rotate size
    window = SDL_CreateWindow("Deskman Robot", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, screen_width, screen_height, fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
    if (!window) {
        fprintf(stderr, "Window could not be created: %s\n", SDL_GetError());
        IMG_Quit();
        SDL_Quit();
        return false;
    }

    // Use the desktop size the window actually covered
    SDL_GetWindowSize(window, &screen_width, &screen_height);

    // Hide cursor in fullscreen mode
    if (fullscreen) {
        SDL_ShowCursor(SDL_DISABLE);
    }

    // Create renderer for the window
    renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED);
    if (!renderer) {
        fprintf(stderr, "Renderer could not be created: %s\n", SDL_GetError());
        SDL_DestroyWindow(window);
        window = nullptr;
        IMG_Quit();
        SDL_Quit();
        return false;
    }

    // Clear screen
    SDL_SetRenderDrawColor(renderer, 255, 255, 255, 255);
    SDL_RenderClear(renderer);
    SDL_RenderPresent(renderer);
    SDL_Delay(16);
    return true;
}

bool close_window() {
    // Clean up
    if (renderer) {
        SDL_DestroyRenderer(renderer);
        renderer = nullptr;
    }
    if (window) {
        SDL_DestroyWindow(window);
        window = nullptr;
    }
    IMG_Quit();
    SDL_Quit();
    return true;
}

void draw_text(const char* text, int x, int y, TTF_Font* font, SDL_Color color) {
    if (!text || !font) return;
    
    SDL_Surface* textSurface = TTF_RenderText_Solid(font, text, color);
    if (textSurface != NULL) {
        SDL_Texture* textTexture = SDL_CreateTextureFromSurface(renderer, textSurface);
        SDL_Rect textRect = {x, y, textSurface->w, textSurface->h};
        SDL_RenderCopy(renderer, textTexture, NULL, &textRect);
        SDL_FreeSurface(textSurface);
        SDL_DestroyTexture(textTexture);
    }
}

// True when a tap lands inside a button
static bool tap_in_rect(int x, int y, SDL_Rect rect) {
    return x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h;
}

// Draw a filled button with centered label
static void draw_bar_button(SDL_Rect rect, const char* label, SDL_Color fill, TTF_Font* font) {
    SDL_SetRenderDrawColor(renderer, fill.r, fill.g, fill.b, fill.a);
    SDL_RenderFillRect(renderer, &rect);
    if (!font || !label) return;
    int text_width = 0;
    int text_height = 0;
    if (TTF_SizeText(font, label, &text_width, &text_height) != 0) return;
    draw_text(label, rect.x + (rect.w - text_width) / 2, rect.y + (rect.h - text_height) / 2, font, {255, 255, 255, 255});
}

// Place Call on the right of the bottom bar
static SDL_Rect call_button_rect() {
    int bar_y = screen_height - BOTTOM_BAR_HEIGHT;
    return {screen_width - BOTTOM_BAR_PAD - EXIT_BUTTON_WIDTH, bar_y + BOTTOM_BAR_PAD, EXIT_BUTTON_WIDTH, BOTTOM_BAR_HEIGHT - BOTTOM_BAR_PAD * 2};
}

// Place Exit to the left of Call
static SDL_Rect exit_button_rect() {
    SDL_Rect call_rect = call_button_rect();
    return {call_rect.x - BOTTOM_BAR_PAD - EXIT_BUTTON_WIDTH, call_rect.y, call_rect.w, call_rect.h};
}

// Status bar with the last log line, battery text, Call, and Exit while the overlay is up
void draw_bottom_bar(const char* battery, TTF_Font* font, bool show_exit) {
    if (!show_exit && !g_status_bar_visible) return;
    int bar_y = screen_height - BOTTOM_BAR_HEIGHT;
    SDL_Rect bar = {0, bar_y, screen_width, BOTTOM_BAR_HEIGHT};
    SDL_SetRenderDrawColor(renderer, 180, 180, 180, 255);
    SDL_RenderFillRect(renderer, &bar);
    SDL_SetRenderDrawColor(renderer, 120, 120, 120, 255);
    SDL_RenderDrawRect(renderer, &bar);

    // Battery above the last log line on the left, clipped so it does not run into the buttons
    string log_line = last_log_line();
    bool have_log = !log_line.empty() && font;
    bool have_battery = battery && battery[0] && font;
    int log_width = 0;
    int log_height = 0;
    int battery_width = 0;
    int battery_height = 0;
    if (have_log) TTF_SizeText(font, log_line.c_str(), &log_width, &log_height);
    if (have_battery) TTF_SizeText(font, battery, &battery_width, &battery_height);
    int block_height = log_height + battery_height;
    if (have_log && have_battery) block_height += BOTTOM_BAR_LINE_GAP;
    int text_y = bar_y + (BOTTOM_BAR_HEIGHT - block_height) / 2;
    int text_right = (show_exit ? exit_button_rect() : call_button_rect()).x - BOTTOM_BAR_PAD;
    int clip_width = text_right - BOTTOM_BAR_PAD;
    if (clip_width < 0) clip_width = 0;
    SDL_Rect text_clip = {BOTTOM_BAR_PAD, bar_y, clip_width, BOTTOM_BAR_HEIGHT};
    SDL_RenderSetClipRect(renderer, &text_clip);
    if (have_battery) {
        draw_text(battery, BOTTOM_BAR_PAD, text_y, font, {0, 0, 0, 255});
        text_y += battery_height + BOTTOM_BAR_LINE_GAP;
    }
    if (have_log) draw_text(log_line.c_str(), BOTTOM_BAR_PAD, text_y, font, {0, 0, 0, 255});
    SDL_RenderSetClipRect(renderer, nullptr);

    // Call on the right, Exit to its left while the overlay is up
    if (show_exit) draw_bar_button(exit_button_rect(), "Exit", {180, 40, 40, 255}, font);
    draw_bar_button(call_button_rect(), "Call", {40, 90, 180, 255}, font);
}

// True when a tap lands on Exit
bool tap_is_exit(int x, int y) {
    return tap_in_rect(x, y, exit_button_rect());
}

// True when a tap lands on Call
bool tap_is_call(int x, int y) {
    return tap_in_rect(x, y, call_button_rect());
}

// True when the grey bar and Call are on screen
bool status_bar_visible() {
    return g_status_bar_visible;
}

// Show or hide the grey bar and Call
void set_status_bar_visible(bool visible) {
    g_status_bar_visible = visible;
}

void draw_coordinate_text(Face* face) {
    // Draw coordinate text
    char coordText[100];
    snprintf(coordText, sizeof(coordText), "Head X: %.1f  Y: %.1f", face->currentHeadX * HEAD_LOOK_DEGREES, face->currentHeadY * HEAD_LOOK_DEGREES);
    SDL_Color textColor = {0, 0, 0, 255};
    //draw_text(coordText, 10, 10, face->font, textColor);
}
