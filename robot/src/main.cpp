// Deskman robot.
// Face, servos, and camera tracking.

#include "face.h"
#include "screen.h"
#include "servos.h"
#include "renderer.h"
#include "tracker.hpp"
#include "config.h"
#include "interface.h"
#include "fan.h"
#include "battery.h"
#include <iostream>
#include <thread>
#include <chrono>
#include <mutex>
#include <cstdlib>
#include <cstring>
#include <signal.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <filesystem>
#include <fstream>
#include <cctype>
#include <system_error>
#include <vector>
#include <iterator>

using namespace std;
using namespace std::chrono;
using namespace std::this_thread;

static const char* DEFAULT_DISPLAY = ":0";
static const int SCREEN_WAIT_MS = 500;
static const int TALK_EARLY_WAIT_MS = 3000;
static const int TALK_EARLY_POLL_MS = 100;
static const int TALK_STOP_WAIT_MS = 200;
static const int TALK_STOP_POLL_MS = 50;
static const int MAX_FPS = 30;
static const char* TALK_SCRIPT_NAME = "talk.py";
static const char* TALK_PYTHON_FROM_REPO = "talk/.venv/bin/python";
static const char* TALK_SCRIPT_FROM_REPO = "talk/talk.py";

bool show_window = true;
bool use_camera = true;
bool show_camera = false;
volatile bool g_quit = false;

static pid_t g_talk_pid = -1;
static bool g_no_talk = false;
static bool g_cold_talk = false;
static bool g_call_paused = false;
static bool g_call_had_talk = false;

VectorRenderer vectorRenderer;

static int parse_arguments(int argc, char **argv, bool& sweep_only, bool& no_servos, bool& print_servos);
static void setup_display_env();
static void rotate_screen();
static string repo_path(const char* relative);
static pid_t find_talk_pid();
static bool start_talk_process(bool cold_talk);
static void stop_talk_process();
static void reap_talk_process();
static void wait_for_talk_early_exit();
static void signalHandler(int signal);
static void run_robot_loop(FaceTracker& faceTracker, bool& quit);
static void apply_call_handoff(FaceTracker& faceTracker);

int main(int argc, char **argv) {
    setup_display_env();

    // Register signal handlers for clean shutdown
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
    signal(SIGHUP, signalHandler);
    signal(SIGQUIT, signalHandler);
    signal(SIGPIPE, SIG_IGN);

    // Load config, then overlay flags
    AppConfig config = loadConfig();
    use_camera = config.useCamera;
    bool sweep_only = false;
    bool no_servos = false;
    bool print_servos = false;
    int parse_result = parse_arguments(argc, argv, sweep_only, no_servos, print_servos);
    if (parse_result != 0) return parse_result == 1 ? 0 : 1;

    // Temporary, leave the camera for teleport
    use_camera = false;

    // Relax servos on any later exit
    atexit([]() { relax_servos(); });

    // Print positions only, skip the rest of the robot
    if (print_servos) {
        relax_servos();
        start_servo_position_log();
        while (!g_quit) {
            sleep_for(milliseconds(100));
        }
        stop_servo_position_log();
        return 0;
    }

    // Rotate the screen and keep touch aligned
    rotate_screen();

    // Spawn talk before servos and camera so a fast talk.py crash is not mid libcamera
    bool quit = false;
    if (!sweep_only && !g_no_talk) {
        start_talk_process(g_cold_talk);
        wait_for_talk_early_exit();
    }

    // Relax servos when travel limits are missing from config.json
    if (!config.has_servo_limits) {
        no_servos = true;
        printf("Servos disabled, config.json needs pan_min, pan_max, tilt_min, tilt_max, hat_min, hat_max\n");
    }

    // Connect to servos, or relax them and leave them disabled
    if (no_servos) {
        relax_servos();
    } else if (open_servos() != 0) {
        if (sweep_only) return 1;
    }

    // Servo sweep test around center, then exit
    if (sweep_only) {
        if (no_servos) {
            printf("Servos disabled, not sweeping\n");
            return 0;
        }
        sweep_servos();
        return 0;
    }

    // Listen so other programs can move the head and pause the camera
    start_interface();

    // Create face tracker after args so --camera / --no-camera apply
    FaceTracker faceTracker(show_camera, use_camera);
    rotate_screen();

    // Create window, continue headless if display is unavailable
    if (show_window && !create_window()) {
        show_window = false;
    }

    // Create face
    face = create_face(screen_width, screen_height);
    reset_face_animation(&face);

    // Start face tracking if camera is available
    if (use_camera && faceTracker.isCameraAvailable()) {
        faceTracker.startTracking();
    }

    // Log positions after startup prints, so they do not interleave
    if (no_servos) start_servo_position_log();

    run_robot_loop(faceTracker, quit);

    // Stop child talk, sockets, tracking, then drop torque
    quit = true;
    g_quit = true;
    cout << "Quit" << endl;
    stop_talk_process();
    stop_interface();
    stop_servo_position_log();
    faceTracker.stopTracking();
    relax_servos();
    if (show_window) close_window();
    return 0;
}

// Parse flags, return 1 for help, -1 for error, 0 to continue
static int parse_arguments(int argc, char **argv, bool& sweep_only, bool& no_servos, bool& print_servos) {
    g_no_talk = false;
    g_cold_talk = false;
    for (int i = 1; i < argc; i++) {
        string arg = argv[i];
        if (arg == "--no-talk") {
            g_no_talk = true;
        } else if (arg == "--cold") {
            g_cold_talk = true;
        } else if (arg == "--servos") {
            sweep_only = true;
        } else if (arg == "--no-servos") {
            no_servos = true;
        } else if (arg == "--servos-print") {
            print_servos = true;
        } else if (arg == "--id") {
            if (i + 2 >= argc) {
                cerr << "Error: --id needs old and new ID" << endl;
                return -1;
            }
            int old_id = atoi(argv[++i]);
            int new_id = atoi(argv[++i]);
            return set_servo_id(old_id, new_id) == 0 ? 1 : -1;
        } else if (arg == "--camera") {
            show_camera = true;
        } else if (arg == "--no-camera") {
            use_camera = false;
        } else if (arg == "--help" || arg == "-h") {
            cout << "Usage: " << argv[0] << " [options]" << endl;
            cout << "Options:" << endl;
            cout << "  --no-talk            Do not spawn talk.py" << endl;
            cout << "  --cold               Skip talk.py text model warm-up" << endl;
            cout << "  --servos             Sweep servos around center a bit, then exit" << endl;
            cout << "  --servos-print       Relax servos and print positions every second, then exit" << endl;
            cout << "  --no-servos          Relax servos, print positions every second, run without moving them" << endl;
            cout << "  --id OLD NEW         Set a servo ID, OLD is 0 to address every servo on the bus" << endl;
            cout << "  --camera             Show face-tracking video feed on the display" << endl;
            cout << "  --no-camera          Do not open a camera, face tracking off" << endl;
            cout << "  --help, -h           Show this help message" << endl;
            return 1;
        } else {
            cerr << "Error: unknown option " << arg << endl;
            return -1;
        }
    }
    return 0;
}

// Draw the face, track people, and handle keys until quit
static void run_robot_loop(FaceTracker& faceTracker, bool& quit) {
    SDL_Event event;
    while (!quit && !g_quit) {
        reap_talk_process();
        check_fan();
        check_battery();
        apply_call_handoff(faceTracker);

        // Process keyboard input on the main thread when a window exists
        while (show_window && SDL_PollEvent(&event) != 0) {
            if (event.type == SDL_QUIT ||
               (event.type == SDL_KEYDOWN && event.key.keysym.sym == SDLK_ESCAPE) ||
               (event.type == SDL_KEYDOWN && event.key.keysym.sym == SDLK_q)) {
                cout << "Quitting..." << endl;
                quit = true;
            }

            // Toggle camera face tracking on/off with c
            if (event.type == SDL_KEYDOWN && event.key.keysym.sym == SDLK_c) {
                use_camera = !use_camera;
                if (use_camera) {
                    if (faceTracker.initializeCamera()) {
                        faceTracker.startTracking();
                        setStatus("Face tracking enabled");
                    } else {
                        use_camera = false;
                        setStatus("Face tracking failed - no camera available");
                    }
                } else {
                    faceTracker.stopCamera();
                    setStatus("Face tracking disabled");
                }
                AppConfig current = loadConfig();
                current.useCamera = use_camera;
                saveConfig(current);
            }

            handle_servo_keyboard_input(&event, &face);
            handle_call_event(event);
        }

        // Point eyes and head at the tracked face
        float faceX, faceY;
        bool hasFaceTracking = use_camera && faceTracker.isCameraAvailable() && faceTracker.getFacePosition(faceX, faceY);
        if (hasFaceTracking) {
            face.lookTiltX = -faceX * 30;
            face.lookTiltY = faceY * 30;
            move_head(-faceX * 20, faceY * 20, 0);
        }

        update_face_animation(&face, 1000.0f / MAX_FPS);

        // Draw face when a window is available
        if (show_window && renderer) {
            SDL_SetRenderDrawColor(renderer, 255, 255, 255, 255);
            SDL_RenderClear(renderer);
            vectorRenderer.render(renderer);

            string status;
            unique_lock<mutex> lock(statusMutex);
            status = currentStatus;
            lock.unlock();
            if (!status.empty()) draw_text(status.c_str(), 10, 10, face.font, {0, 0, 0, 255});

            string fan_warning = fan_warning_text();
            if (!fan_warning.empty()) draw_text(fan_warning.c_str(), 10, 50, face.font, {200, 0, 0, 255});

            string temperature_warning = temperature_warning_text();
            if (!temperature_warning.empty()) draw_text(temperature_warning.c_str(), 10, 90, face.font, {200, 0, 0, 255});

            // Pack voltage from the INA219
            string battery = battery_text();
            if (!battery.empty()) draw_text(battery.c_str(), 10, screen_height - 40, face.font, {0, 0, 0, 255});

            if (use_camera && faceTracker.isCameraAvailable()) faceTracker.updateWindow();

            SDL_RenderPresent(renderer);

            static Uint32 lastFrameTime = SDL_GetTicks();
            Uint32 currentTime = SDL_GetTicks();
            Uint32 frameTime = currentTime - lastFrameTime;
            if (frameTime < (1000 / MAX_FPS)) SDL_Delay((1000 / MAX_FPS) - frameTime);
            lastFrameTime = SDL_GetTicks();
        } else {
            sleep_for(milliseconds(1000 / MAX_FPS));
        }
    }
}

// Pause camera and talk for other programs, or restore them after
static void apply_call_handoff(FaceTracker& faceTracker) {
    int command = take_call_handoff();
    if (command == CALL_HANDOFF_NONE) return;

    if (command == CALL_HANDOFF_PAUSE) {
        if (!g_call_paused) {
            faceTracker.stopCamera();
            g_call_had_talk = !g_no_talk && g_talk_pid > 0;
            if (g_call_had_talk) stop_talk_process();
            g_call_paused = true;
            cout << "Paused camera and talk." << endl;
        }
        complete_call_handoff(true);
        return;
    }

    if (g_call_paused) {
        if (use_camera && faceTracker.initializeCamera()) faceTracker.startTracking();
        if (g_call_had_talk) start_talk_process(g_cold_talk);
        g_call_paused = false;
        g_call_had_talk = false;
        setStatus("");
        cout << "Resumed camera and talk." << endl;
    }
    complete_call_handoff(true);
}

static void setup_display_env() {

    // Default DISPLAY to the local HDMI seat
    if (getenv("DISPLAY") == nullptr || getenv("DISPLAY")[0] == '\0') {
        setenv("DISPLAY", DEFAULT_DISPLAY, 1);
    }

    // Build the per-user runtime path
    string runtime = "/run/user/" + to_string(getuid());

    // Default XDG_RUNTIME_DIR for pulse and similar
    if (getenv("XDG_RUNTIME_DIR") == nullptr || getenv("XDG_RUNTIME_DIR")[0] == '\0') {
        setenv("XDG_RUNTIME_DIR", runtime.c_str(), 1);
    }

    // Point at the gdm Xauthority file when present
    if (getenv("XAUTHORITY") == nullptr || getenv("XAUTHORITY")[0] == '\0') {
        string xauth = runtime + "/gdm/Xauthority";
        if (filesystem::exists(xauth)) setenv("XAUTHORITY", xauth.c_str(), 1);
    }
}

static void rotate_screen() {
#ifdef __linux__
    const int tries = 2;
    bool connected = false;

    // Wait until DP-1 is connected after login or service start
    for (int try_index = 0; try_index < tries; try_index++) {
        if (system("xrandr --query 2>/dev/null | grep -q '^DP-1 connected'") == 0) {
            connected = true;
            break;
        }
        sleep_for(milliseconds(SCREEN_WAIT_MS));
    }

    // Skip quietly when this machine has no Waveshare panel
    if (!connected) return;

    // Apply left rotation, use right for the other direction
    system("xrandr --output DP-1 --rotate left 2>/dev/null");

    // Wait until xrandr reports left, GNOME can overwrite it during login
    for (int try_index = 0; try_index < tries; try_index++) {
        if (system("xrandr --query 2>/dev/null | grep -q '^DP-1 connected.* left ('") == 0) break;
        system("xrandr --output DP-1 --rotate left 2>/dev/null");
        sleep_for(milliseconds(SCREEN_WAIT_MS));
    }

    // Keep the touch device mapped to the rotated output
    system("xinput map-to-output \"WaveShare WS170120\" DP-1 2>/dev/null");
#endif
}

static void signalHandler(int) {
    g_quit = true;
}

static string repo_path(const char* relative) {
    error_code error;
    filesystem::path executable = filesystem::read_symlink("/proc/self/exe", error);
    if (error) return relative;

    // robot/build/robot sits two folders under the repo root
    filesystem::path repo = executable.parent_path().parent_path().parent_path();
    return (repo / relative).string();
}

static pid_t find_talk_pid() {
    error_code error;
    pid_t my_pid = getpid();
    for (const auto& entry : filesystem::directory_iterator("/proc", error)) {
        if (!entry.is_directory(error)) {
            continue;
        }

        // /proc pids are numeric directory names
        string name = entry.path().filename().string();
        if (name.empty()) {
            continue;
        }
        bool is_pid = true;
        for (unsigned char character : name) {
            if (!isdigit(character)) {
                is_pid = false;
                break;
            }
        }
        if (!is_pid) {
            continue;
        }
        pid_t pid = stoi(name);
        if (pid == my_pid) {
            continue;
        }

        // cmdline is null-separated argv
        ifstream in(entry.path() / "cmdline", ios::binary);
        string cmdline((istreambuf_iterator<char>(in)), istreambuf_iterator<char>());
        if (cmdline.empty()) {
            continue;
        }

        // Split argv tokens
        vector<string> tokens;
        string token;
        for (char character : cmdline) {
            if (character == '\0') {
                if (!token.empty()) {
                    tokens.push_back(token);
                    token.clear();
                }
            } else {
                token += character;
            }
        }
        if (!token.empty()) {
            tokens.push_back(token);
        }
        if (tokens.empty()) {
            continue;
        }

        // Match python running talk.py, or a shebang ./talk.py
        string executable = tokens[0];
        size_t slash = executable.find_last_of('/');
        if (slash != string::npos) {
            executable = executable.substr(slash + 1);
        }
        bool is_talk = executable == TALK_SCRIPT_NAME;
        if (!is_talk && executable.rfind("python", 0) == 0) {
            for (const string& argument : tokens) {
                if (argument.ends_with(TALK_SCRIPT_NAME)) {
                    is_talk = true;
                    break;
                }
            }
        }
        if (is_talk) {
            return pid;
        }
    }
    return -1;
}

static bool start_talk_process(bool cold_talk) {
    pid_t existing_pid = find_talk_pid();
    if (existing_pid > 0) {
        cout << "talk.py already running, pid " << existing_pid << endl;
        return true;
    }

    string talk_python = repo_path(TALK_PYTHON_FROM_REPO);
    string talk_script = repo_path(TALK_SCRIPT_FROM_REPO);
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork talk.py");
        return false;
    }
    if (pid == 0) {

        // Child: drop inherited fds above stdin/stdout/stderr, then exec talk.py
        struct rlimit limit{};
        int max_fd = 1024;
        if (getrlimit(RLIMIT_NOFILE, &limit) == 0 && limit.rlim_cur > 0 && limit.rlim_cur < 100000) {
            max_fd = static_cast<int>(limit.rlim_cur);
        }
        for (int fd = 3; fd < max_fd; fd++) close(fd);

        if (cold_talk) {
            execl(talk_python.c_str(), talk_python.c_str(), talk_script.c_str(), "--no-replay-robot", "--cold", static_cast<char*>(nullptr));
        } else {
            execl(talk_python.c_str(), talk_python.c_str(), talk_script.c_str(), "--no-replay-robot", static_cast<char*>(nullptr));
        }
        cerr << "Error: talk.py: " << strerror(errno) << endl;
        _exit(127);
    }
    g_talk_pid = pid;
    cout << "Starting talk.py..." << endl;
    return true;
}

static void reap_talk_process() {
    if (g_talk_pid <= 0) return;
    int status = 0;
    pid_t waited = waitpid(g_talk_pid, &status, WNOHANG);
    if (waited <= 0) return;
    if (WIFEXITED(status)) {
        if (WEXITSTATUS(status) != 127) {
            cout << "talk.py exited with code " << WEXITSTATUS(status) << endl;
        }
    } else if (WIFSIGNALED(status)) {
        cout << "talk.py killed by signal " << WTERMSIG(status) << endl;
    } else {
        cout << "talk.py exited" << endl;
    }
    g_talk_pid = -1;
}

static void wait_for_talk_early_exit() {
    int waited_ms = 0;
    while (g_talk_pid > 0 && waited_ms < TALK_EARLY_WAIT_MS) {
        reap_talk_process();
        if (g_talk_pid <= 0) return;
        sleep_for(milliseconds(TALK_EARLY_POLL_MS));
        waited_ms += TALK_EARLY_POLL_MS;
    }
}

static void stop_talk_process() {
    if (g_talk_pid <= 0) return;
    cout << "Stopping talk.py pid " << g_talk_pid << endl;
    kill(g_talk_pid, SIGTERM);
    int status = 0;
    int waited_ms = 0;
    while (waited_ms < TALK_STOP_WAIT_MS) {
        pid_t waited = waitpid(g_talk_pid, &status, WNOHANG);
        if (waited == g_talk_pid) {
            g_talk_pid = -1;
            return;
        }
        sleep_for(milliseconds(TALK_STOP_POLL_MS));
        waited_ms += TALK_STOP_POLL_MS;
    }
    kill(g_talk_pid, SIGKILL);
    waitpid(g_talk_pid, &status, WNOHANG);
    g_talk_pid = -1;
}
