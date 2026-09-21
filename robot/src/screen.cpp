// Local
#include "screen.h"
#include "face.h"
#include "interface.h"

// SDL
#include <SDL2/SDL_ttf.h>

// System
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <mutex>
#include <string>
#include <system_error>
#include <thread>

// Posix
#include <fcntl.h>
#include <unistd.h>

// Namespace
using namespace std;

// Fall back to the local HDMI seat when DISPLAY is unset
static const char* DEFAULT_DISPLAY = ":0";

// Status bar size and padding
static const int STATUS_BAR_HEIGHT = 96;
static const int STATUS_BAR_PAD = 12;
static const int STATUS_BAR_LINE_GAP = 4;

// Status bar and button colors
static const SDL_Color BAR_FILL_COLOR = {180, 180, 180, 255};
static const SDL_Color BAR_BORDER_COLOR = {120, 120, 120, 255};
static const SDL_Color BAR_TEXT_COLOR = {0, 0, 0, 255};
static const SDL_Color CLEAR_COLOR = {255, 255, 255, 255};

// Skip this much old history on the first read, and clip a long line
static const int LOG_TAIL_BYTES = 8192;
static const int LOG_LINE_MAX = 160;
static const char* LOG_FILE_NAME = "log.txt";

// Log buffer size, and the mode the log file is created with
static const int LOG_BUFFER_SIZE = 4096;
static const int LOG_FILE_MODE = 0644;

// Room for the start stamp, and the head position text
static const int STAMP_SIZE = 64;
static const int COORDINATE_TEXT_SIZE = 100;

// Hold the very first frame long enough for the compositor to show it
static const int FIRST_FRAME_DELAY_MS = 16;

// Names for the start stamp, and the offsets tm reports against
static const char* MONTH_NAMES[] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
};
static const char* DAY_NAMES[] = {
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
};
static const int TM_YEAR_BASE = 1900;
static const int HOURS_PER_HALF_DAY = 12;

// A dead IBus socket, GNOME pops its touch keyboard when SDL takes input method focus
static const char* DEAD_IBUS_ADDRESS = "unix:path=/nonexistent";

// Screen globals
SDL_Window* window = nullptr;
SDL_Renderer* renderer = nullptr;
int screen_width = 800;
int screen_height = 600;

// Status text
string currentStatus;
mutex statusMutex;

// Grey bar starts visible, a tap on the face hides it
static bool g_status_bar_visible = true;

// Console, log file, and the read end of the tee pipe
static int g_log_console_fd = -1;
static int g_log_file_fd = -1;
static int g_log_pipe_read = -1;

// Invisible pointer so the compositor does not draw one over the face
static SDL_Cursor* g_blank_cursor = nullptr;

// Tail of log.txt for the on-screen line
static string g_last_log_line;
static string g_log_pending;
static uintmax_t g_log_offset = 0;
static bool g_log_opened = false;

// Later in this file
static string robot_log_path();
static void write_robot_log_loop();
static void write_log_chunk(int descriptor, const char* buffer, ssize_t count);
static void install_blank_cursor();
static string log_display_line();
static string clip_log_line(const string& text);

// Append stdout and stderr to log.txt, and still print to the console
void start_robot_log() {
    string path = robot_log_path();

    // Keep the original console so lines still print when run from a terminal
    int console_fd = dup(STDOUT_FILENO);
    if (console_fd < 0)
        return;

    // Create or append log.txt
    int log_fd = open(path.c_str(), O_WRONLY | O_CREAT | O_APPEND, LOG_FILE_MODE);
    if (log_fd < 0) {
        close(console_fd);
        return;
    }

    // Everything written goes through this pipe, so it can be copied twice
    int pipe_fds[2];
    if (pipe(pipe_fds) != 0) {
        close(console_fd);
        close(log_fd);
        return;
    }

    // Hand the read end to a worker that tees it
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

    // Work out the local time for the start stamp
    time_t now = time(nullptr);
    tm local_time{};
    localtime_r(&now, &local_time);

    // Turn the hour into twelve hour form
    int hour = local_time.tm_hour;
    const char* suffix = hour >= HOURS_PER_HALF_DAY ? "pm" : "am";
    int hour12 = hour % HOURS_PER_HALF_DAY;
    if (hour12 == 0)
        hour12 = HOURS_PER_HALF_DAY;

    // Blank line and start stamp
    char stamp[STAMP_SIZE];
    snprintf(stamp, sizeof(stamp), "%s %s %d %d, %d:%02d%s", DAY_NAMES[local_time.tm_wday], MONTH_NAMES[local_time.tm_mon], local_time.tm_mday, local_time.tm_year + TM_YEAR_BASE, hour12, local_time.tm_min, suffix);
    cout << endl;
    cout << "=== Robot on " << stamp << " ===" << endl;
}

// Get log path
static string robot_log_path() {
    error_code error;
    filesystem::path executable = filesystem::read_symlink("/proc/self/exe", error);
    if (error)
        return string("../") + LOG_FILE_NAME;
    return (executable.parent_path().parent_path() / LOG_FILE_NAME).string();
}

// Copy each chunk to the console and to log.txt
static void write_robot_log_loop() {
    char buffer[LOG_BUFFER_SIZE];
    while (true) {
        ssize_t count = read(g_log_pipe_read, buffer, sizeof(buffer));
        if (count <= 0)
            break;
        write_log_chunk(g_log_console_fd, buffer, count);
        write_log_chunk(g_log_file_fd, buffer, count);
    }
}

// Write one chunk, skipping a descriptor that was never opened
static void write_log_chunk(int descriptor, const char* buffer, ssize_t count) {
    if (descriptor < 0)
        return;
    ssize_t written = write(descriptor, buffer, count);
    (void)written;
}

// Hide the pointer, and replace it with an empty cursor
void hide_cursor() {
    SDL_ShowCursor(SDL_DISABLE);
    install_blank_cursor();
    if (g_blank_cursor)
        SDL_SetCursor(g_blank_cursor);
}

// One transparent pixel, so Wayland still has a cursor to hide
static void install_blank_cursor() {
    if (g_blank_cursor)
        return;
    SDL_Surface* surface = SDL_CreateRGBSurfaceWithFormat(0, 1, 1, 32, SDL_PIXELFORMAT_RGBA32);
    if (!surface)
        return;
    SDL_FillRect(surface, nullptr, SDL_MapRGBA(surface->format, 0, 0, 0, 0));
    g_blank_cursor = SDL_CreateColorCursor(surface, 0, 0);
    SDL_FreeSurface(surface);
}

// Create window
bool create_window() {
    // Cover the whole screen on the robot, use a plain window elsewhere
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
        if (filesystem::exists(xauth))
            setenv("XAUTHORITY", xauth.c_str(), 1);
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

    // Keep the pointer off the face
    hide_cursor();

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
    SDL_SetRenderDrawColor(renderer, CLEAR_COLOR.r, CLEAR_COLOR.g, CLEAR_COLOR.b, CLEAR_COLOR.a);
    SDL_RenderClear(renderer);
    SDL_RenderPresent(renderer);
    SDL_Delay(FIRST_FRAME_DELAY_MS);
    return true;
}

// Draw text, in UTF-8 so curly quotes render
void draw_text(const char* text, int x, int y, TTF_Font* font, SDL_Color color) {
    if (!text || !font)
        return;

    // Turn the text into a texture, blit it, then let both go
    SDL_Surface* textSurface = TTF_RenderUTF8_Solid(font, text, color);
    if (textSurface != NULL) {
        SDL_Texture* textTexture = SDL_CreateTextureFromSurface(renderer, textSurface);
        SDL_Rect textRect = {x, y, textSurface->w, textSurface->h};
        SDL_RenderCopy(renderer, textTexture, NULL, &textRect);
        SDL_FreeSurface(textSurface);
        SDL_DestroyTexture(textTexture);
    }
}

// Draw status bar
void draw_status_bar(const char* battery, TTF_Font* font, bool keep_visible) {
    if (!keep_visible && !g_status_bar_visible && !menu_open())
        return;

    // Fill the bar
    int bar_y = screen_height - STATUS_BAR_HEIGHT;
    SDL_Rect bar = {0, bar_y, screen_width, STATUS_BAR_HEIGHT};
    SDL_SetRenderDrawColor(renderer, BAR_FILL_COLOR.r, BAR_FILL_COLOR.g, BAR_FILL_COLOR.b, BAR_FILL_COLOR.a);
    SDL_RenderFillRect(renderer, &bar);
    SDL_SetRenderDrawColor(renderer, BAR_BORDER_COLOR.r, BAR_BORDER_COLOR.g, BAR_BORDER_COLOR.b, BAR_BORDER_COLOR.a);
    SDL_RenderDrawRect(renderer, &bar);

    // Measure both lines so the block can be centred in the strip
    string log_line = last_log_line();
    bool have_log = !log_line.empty() && font;
    bool have_battery = battery && battery[0] && font;
    int log_width = 0;
    int log_height = 0;
    int battery_width = 0;
    int battery_height = 0;
    if (have_log)
        TTF_SizeUTF8(font, log_line.c_str(), &log_width, &log_height);
    if (have_battery)
        TTF_SizeUTF8(font, battery, &battery_width, &battery_height);
    int block_height = log_height + battery_height;
    if (have_log && have_battery)
        block_height += STATUS_BAR_LINE_GAP;
    int text_y = bar_y + (STATUS_BAR_HEIGHT - block_height) / 2;

    // Clip the text so a long line does not run into the menu button
    int text_right = menu_button_left() - STATUS_BAR_PAD;
    int clip_width = text_right - STATUS_BAR_PAD;
    if (clip_width < 0)
        clip_width = 0;
    SDL_Rect text_clip = {STATUS_BAR_PAD, bar_y, clip_width, STATUS_BAR_HEIGHT};
    SDL_RenderSetClipRect(renderer, &text_clip);

    // Battery above the last log line, on the left
    if (have_battery) {
        draw_text(battery, STATUS_BAR_PAD, text_y, font, BAR_TEXT_COLOR);
        text_y += battery_height + STATUS_BAR_LINE_GAP;
    }
    if (have_log)
        draw_text(log_line.c_str(), STATUS_BAR_PAD, text_y, font, BAR_TEXT_COLOR);
    SDL_RenderSetClipRect(renderer, nullptr);

    // Menu button and the popup above it
    draw_menu(font);
}

// Newest line from log.txt, talk and robot both write there
string last_log_line() {
    error_code error;
    string path = robot_log_path();
    uintmax_t size = filesystem::file_size(path, error);
    if (error)
        return log_display_line();

    // File was truncated, read from the start
    if (size < g_log_offset) {
        g_log_offset = 0;
        g_log_pending.clear();
        g_log_opened = false;
    }

    // First open, skip the old history and keep the tail
    if (!g_log_opened) {
        g_log_opened = true;
        if (size > static_cast<uintmax_t>(LOG_TAIL_BYTES))
            g_log_offset = size - LOG_TAIL_BYTES;
    }
    if (size == g_log_offset)
        return log_display_line();

    // Read whatever was appended since last time
    ifstream input(path);
    if (!input)
        return log_display_line();
    input.seekg(static_cast<streamoff>(g_log_offset));
    string chunk((istreambuf_iterator<char>(input)), istreambuf_iterator<char>());
    g_log_offset = size;

    // Treat a carriage return as a line end, progress bars use them
    for (char& character : chunk) {
        if (character == '\r')
            character = '\n';
    }
    g_log_pending += chunk;

    // Keep the last non-empty line
    size_t newline;
    while ((newline = g_log_pending.find('\n')) != string::npos) {
        string line = clip_log_line(g_log_pending.substr(0, newline));
        g_log_pending.erase(0, newline + 1);
        if (!line.empty())
            g_last_log_line = line;
    }
    return log_display_line();
}

// Characters written since the last newline, so a line appears as it is typed
static string log_display_line() {
    string partial = clip_log_line(g_log_pending);
    if (!partial.empty())
        return partial;
    return g_last_log_line;
}

// Drop trailing blanks and cut a long line down to the bar width
static string clip_log_line(const string& text) {
    string line = text;
    while (!line.empty() && (line.back() == ' ' || line.back() == '\t'))
        line.pop_back();
    if (static_cast<int>(line.size()) > LOG_LINE_MAX)
        line.resize(LOG_LINE_MAX);
    return line;
}

// Height of the grey bar
int status_bar_height() {
    return STATUS_BAR_HEIGHT;
}

// Inset of the menu button inside the grey bar
int status_bar_pad() {
    return STATUS_BAR_PAD;
}

// True when the grey bar is on screen
bool status_bar_visible() {
    return g_status_bar_visible;
}

// Show or hide the grey bar
void set_status_bar_visible(bool visible) {
    g_status_bar_visible = visible;
}

// Set status text
void setStatus(const string& status) {
    unique_lock<mutex> lock(statusMutex);

    // Print it once, skipping repeated sleeps and every listen
    bool still_sleeping = currentStatus.find("Sleeping") != string::npos && status.find("Sleeping") != string::npos;
    bool listening = status.find("Listening") != string::npos;
    if (!still_sleeping && !listening && !status.empty())
        cout << status << endl;
    currentStatus = status;
}

// Tear the window down
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
    if (g_blank_cursor) {
        SDL_FreeCursor(g_blank_cursor);
        g_blank_cursor = nullptr;
    }
    IMG_Quit();
    SDL_Quit();
    return true;
}

// Head position readout, the draw is commented out while it is unused
void draw_coordinate_text(Face* face) {
    // Draw coordinate text
    char coordText[COORDINATE_TEXT_SIZE];
    snprintf(coordText, sizeof(coordText), "Head X: %.1f  Y: %.1f", face->currentHeadX * HEAD_LOOK_DEGREES, face->currentHeadY * HEAD_LOOK_DEGREES);
    SDL_Color textColor = BAR_TEXT_COLOR;
    //draw_text(coordText, 10, 10, face->font, textColor);
}
