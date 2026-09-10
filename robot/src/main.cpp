// Deskman robot.
// Face, servos, and camera tracking.

// Local
#include "face.h"
#include "screen.h"
#include "servos.h"
#include "renderer.h"
#include "tracker.h"
#include "config.h"
#include "interface.h"
#include "fan.h"
#include "battery.h"

// System
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <system_error>
#include <thread>
#include <vector>

// Posix
#include <signal.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

// Namespace
using namespace std;
using namespace std::chrono;
using namespace std::this_thread;

// Parsing the flags hands this back when the robot should carry on running
static const int KEEP_RUNNING = -1;

// Fall back to the local HDMI seat when DISPLAY is unset
static const char* DEFAULT_DISPLAY = ":0";

// Frame rate the face is drawn at
static const int MAX_FPS = 30;
static const int FRAME_MS = 1000 / MAX_FPS;

// Wait for the panel to connect, and retry the rotate
static const int SCREEN_WAIT_MS = 500;
static const int SCREEN_ROTATE_TRIES = 2;

// Frames to pump before the slow startup work, so the eyes appear early
static const int STARTUP_FRAMES = 8;

// Ask talk.py to stop, then kill it, the last code says the exec never took
static const int TALK_STOP_WAIT_MS = 200;
static const int TALK_STOP_POLL_MS = 50;
static const int TALK_EXEC_FAILED = 127;

// How far the head and eyes swing for a full face offset
static const int FACE_TRACK_COUNTS = 20;
static const int FACE_LOOK_TILT_DEGREES = 30;

// Poll while printing servo positions
static const int SERVO_LOG_POLL_MS = 100;

// Process and script names to match
static const char* BINARY_NAME = "robot";
static const char* TALK_SCRIPT_NAME = "talk.py";
static const char* TALK_PYTHON_FROM_REPO = "talk/.venv/bin/python";
static const char* TALK_SCRIPT_FROM_REPO = "talk/talk.py";

// Close inherited descriptors in the talk child, ignoring a silly rlimit
static const int DEFAULT_MAX_DESCRIPTORS = 1024;
static const int MAX_SANE_DESCRIPTORS = 100000;

// Longest line of ps output to read
static const int PS_LINE_SIZE = 4096;

// White behind the face, red warnings down the left side
static const SDL_Color BACKGROUND_COLOR = {255, 255, 255, 255};
static const SDL_Color WARNING_COLOR = {200, 0, 0, 255};
static const int WARNING_X = 10;
static const int FAN_WARNING_Y = 10;
static const int TEMPERATURE_WARNING_Y = 50;

// Set from flags, read across the program
bool show_window = true;
bool use_camera = true;
bool show_camera = false;
volatile bool g_quit = false;

// Talk child process and the call handoff state
static pid_t g_talk_pid = -1;
static bool g_no_talk = false;
static bool g_call_paused = false;
static bool g_call_had_talk = false;

// Renderer holding the face shapes
VectorRenderer vectorRenderer;

// Later in this file
static int parse_arguments(int argc, char **argv, bool& sweep_only, bool& no_servos, bool& print_servos);
static void run_robot_loop(FaceTracker& faceTracker, bool& quit);
static void set_up_display();
static void signalHandler(int signal);
static void check_already_running();
static pid_t find_other_running();
static int print_servo_positions();
static void rotate_screen();
static bool display_is_rotated_left();
static void show_face();
static void draw_face();
static int start_servos();
static int sweep_servo_test(bool no_servos);
static bool start_talk_process();
static pid_t find_talk_pid();
static string repo_path(const char* relative);
static void apply_call_handoff(FaceTracker& faceTracker);
static void reap_talk_process();
static void stop_robot(FaceTracker& faceTracker);
static void stop_talk_process();

int main(int argc, char **argv) {
    // Log
    start_robot_log();

    // Set up display
    set_up_display();

    // Register signal handlers for clean shutdown
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
    signal(SIGHUP, signalHandler);
    signal(SIGQUIT, signalHandler);
    signal(SIGPIPE, SIG_IGN);

    // Load config
    AppConfig config = loadConfig();
    use_camera = config.useCamera;

    // Default the flags the parse can turn on
    bool sweep_only = false;
    bool no_servos = false;
    bool print_servos = false;

    // Parse arguments
    int exit_code = parse_arguments(argc, argv, sweep_only, no_servos, print_servos);
    if (exit_code != KEEP_RUNNING)
        return exit_code;

    // Make sure only one robot is running
    check_already_running();

    // First log line
    cout << "Starting robot..." << endl;

    // Relax servos on any later exit
    atexit([]() { relax_servos(); });

    // Print positions only, skip the rest of the robot
    if (print_servos)
        return print_servo_positions();

    // Rotate the screen and keep touch aligned
    rotate_screen();

    // Put the face on screen before the servo bus scan
    if (!sweep_only)
        show_face();

    // Relax servos when travel limits are missing from config.json
    if (!config.has_servo_limits) {
        no_servos = true;
        printf("Servos disabled, config.json needs pan_min, pan_max, tilt_min, tilt_max, hat_min, hat_max\n");
    }

    // Connect to servos, or relax them and leave them disabled
    if (no_servos) {
        relax_servos();
    } else if (start_servos() != 0) {
        if (sweep_only)
            return 1;
    }

    // Servo sweep test around center, then exit
    if (sweep_only)
        return sweep_servo_test(no_servos);

    // Listen so other programs can move the head and pause the camera
    start_interface();

    // Spawn talk after the bus and socket, and before the camera
    if (!g_no_talk)
        start_talk_process();

    // Create face tracker after args so --camera and --no-camera apply
    FaceTracker faceTracker(show_camera, use_camera);
    if (use_camera && faceTracker.isCameraAvailable())
        faceTracker.startTracking();

    // Log positions after startup prints, so they do not interleave
    if (no_servos)
        start_servo_position_log();

    // Draw the face until quit, then put everything down
    bool quit = false;
    run_robot_loop(faceTracker, quit);
    stop_robot(faceTracker);
    return 0;
}

// Parse flags, return the exit code, or KEEP_RUNNING to carry on
static int parse_arguments(int argc, char **argv, bool& sweep_only, bool& no_servos, bool& print_servos) {
    g_no_talk = false;
    for (int i = 1; i < argc; i++) {
        string argument = argv[i];
        if (argument == "--no-talk") {
            g_no_talk = true;
        } else if (argument == "--servos") {
            sweep_only = true;
        } else if (argument == "--no-servos") {
            no_servos = true;
            g_no_talk = true;
            use_camera = false;
            show_camera = false;
        } else if (argument == "--servos-print") {
            print_servos = true;
        } else if (argument == "--id") {
            if (i + 2 >= argc) {
                cerr << "Error: --id needs old and new ID" << endl;
                return 1;
            }
            int old_id = atoi(argv[++i]);
            int new_id = atoi(argv[++i]);
            return set_servo_id(old_id, new_id) == 0 ? 0 : 1;
        } else if (argument == "--camera") {
            show_camera = true;
        } else if (argument == "--no-camera") {
            use_camera = false;
        } else if (argument == "--help" || argument == "-h") {
            cout << "Usage: " << argv[0] << " [options]" << endl;
            cout << "Options:" << endl;
            cout << "  --no-talk            Do not spawn talk.py" << endl;
            cout << "  --servos             Sweep servos around center, scan IDs 1 to 20, then exit" << endl;
            cout << "  --servos-print       Relax servos and print positions every second, then exit" << endl;
            cout << "  --no-servos          Relax servos, print positions every second, no talk.py, no camera, no face tracking" << endl;
            cout << "  --id OLD NEW         Set a servo ID, OLD is 0 to address every servo on the bus" << endl;
            cout << "  --camera             Show face-tracking video feed on the display" << endl;
            cout << "  --no-camera          Do not open a camera, face tracking off" << endl;
            cout << "  --help, -h           Show this help message" << endl;
            return 0;
        } else {
            cerr << "Error: unknown option " << argument << endl;
            return 1;
        }
    }
    return KEEP_RUNNING;
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

            // Toggle camera face tracking on and off with c
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

                // Remember the choice for the next run
                AppConfig current = loadConfig();
                current.useCamera = use_camera;
                saveConfig(current);
            }

            // Hand the event to the servo keys and the Call and Exit buttons
            handle_servo_keyboard_input(&event, &face);
            handle_call_event(event);
        }

        // Point eyes and head at the tracked face until talk goes ready after listening
        float faceX, faceY;
        bool hasFaceTracking = use_camera && listen_open() && faceTracker.isCameraAvailable() && faceTracker.getFacePosition(faceX, faceY);
        if (hasFaceTracking) {
            face.lookTiltX = -faceX * FACE_LOOK_TILT_DEGREES;
            face.lookTiltY = faceY * FACE_LOOK_TILT_DEGREES;

            // Truncate to whole counts, so a small face offset leaves the head still
            int pan_nudge = (int)(-faceX * FACE_TRACK_COUNTS);
            int tilt_nudge = (int)(faceY * FACE_TRACK_COUNTS);
            move_degrees(pan_degrees_from_counts(pan_nudge), tilt_degrees_from_counts(tilt_nudge), 0);
        }

        // Step the blink
        update_face_animation(&face);

        // Sleep out the frame when there is nothing to draw
        if (!show_window || !renderer) {
            sleep_for(milliseconds(FRAME_MS));
            continue;
        }

        // Clear to white, then draw the eyes and mouth
        SDL_SetRenderDrawColor(renderer, BACKGROUND_COLOR.r, BACKGROUND_COLOR.g, BACKGROUND_COLOR.b, BACKGROUND_COLOR.a);
        SDL_RenderClear(renderer);
        vectorRenderer.render(renderer);

        // Warn about a slow fan or a hot CPU
        string fan_warning = fan_warning_text();
        if (!fan_warning.empty())
            draw_text(fan_warning.c_str(), WARNING_X, FAN_WARNING_Y, face.font, WARNING_COLOR);
        string temperature_warning = temperature_warning_text();
        if (!temperature_warning.empty())
            draw_text(temperature_warning.c_str(), WARNING_X, TEMPERATURE_WARNING_Y, face.font, WARNING_COLOR);

        // Last log line, pack voltage, Call, and Exit while the overlay is up
        draw_status_bar(battery_text().c_str(), face.font, call_overlay_open());

        // Show the tracking preview over the face
        if (use_camera && faceTracker.isCameraAvailable())
            faceTracker.updateWindow();
        SDL_RenderPresent(renderer);

        // Hold the frame rate
        static Uint32 lastFrameTime = SDL_GetTicks();
        Uint32 frameTime = SDL_GetTicks() - lastFrameTime;
        if (frameTime < FRAME_MS)
            SDL_Delay(FRAME_MS - frameTime);
        lastFrameTime = SDL_GetTicks();
    }
}

// Point at the local display, its runtime directory, and its X authority
static void set_up_display() {
    // Default DISPLAY to the local HDMI seat
    if (getenv("DISPLAY") == nullptr || getenv("DISPLAY")[0] == '\0') {
        setenv("DISPLAY", DEFAULT_DISPLAY, 1);
    }

    // Default XDG_RUNTIME_DIR for pulse and similar
    string runtime = "/run/user/" + to_string(getuid());
    if (getenv("XDG_RUNTIME_DIR") == nullptr || getenv("XDG_RUNTIME_DIR")[0] == '\0') {
        setenv("XDG_RUNTIME_DIR", runtime.c_str(), 1);
    }

    // Point at the gdm Xauthority file when present
    if (getenv("XAUTHORITY") == nullptr || getenv("XAUTHORITY")[0] == '\0') {
        string xauth = runtime + "/gdm/Xauthority";
        if (filesystem::exists(xauth))
            setenv("XAUTHORITY", xauth.c_str(), 1);
    }
}

// Break the main loop so shutdown runs in order
static void signalHandler(int) {
    g_quit = true;
}

// Make sure only one robot is running
static void check_already_running() {
    pid_t existing_pid = find_other_running();
    if (existing_pid <= 0)
        return;
    cout << "Robot already running, pid " << existing_pid << endl;
    cout << "sudo service robot stop" << endl;
    exit(1);
}

// Find another robot process
static pid_t find_other_running() {
    FILE* pipe = popen("ps aux", "r");
    if (!pipe)
        return -1;

    // Skip the ps header, then parse pid and command
    pid_t my_pid = getpid();
    char line[PS_LINE_SIZE];
    bool header = true;
    pid_t found_pid = -1;
    while (fgets(line, sizeof(line), pipe)) {
        if (header) {
            header = false;
            continue;
        }

        // Skip user, pid, cpu, mem, and the other ps columns so the rest is the command
        stringstream stream(line);
        string user, cpu, memory, vsz, rss, tty, stat, start, time;
        pid_t pid = 0;
        if (!(stream >> user >> pid >> cpu >> memory >> vsz >> rss >> tty >> stat >> start >> time))
            continue;
        if (pid == my_pid)
            continue;

        // Command is the remainder of the line
        string command;
        getline(stream, command);
        if (!command.empty() && command[0] == ' ')
            command.erase(0, 1);

        // Match the robot binary, not robot_service.sh or journalctl
        stringstream command_stream(command);
        string executable;
        command_stream >> executable;
        size_t slash = executable.find_last_of('/');
        if (slash != string::npos)
            executable = executable.substr(slash + 1);
        if (executable == BINARY_NAME) {
            found_pid = pid;
            break;
        }
    }
    pclose(pipe);
    return found_pid;
}

// Relax the servos and print where they are until quit
static int print_servo_positions() {
    relax_servos();
    start_servo_position_log();
    while (!g_quit) {
        sleep_for(milliseconds(SERVO_LOG_POLL_MS));
    }
    stop_servo_position_log();
    return 0;
}

// Rotate to portrait, skip xrandr when already left so the NVIDIA splash does not flash
static void rotate_screen() {
#ifdef __linux__
    // Wait until DP-1 is connected after login or service start
    bool connected = false;
    for (int try_index = 0; try_index < SCREEN_ROTATE_TRIES; try_index++) {
        if (system("xrandr --query 2>/dev/null | grep -q '^DP-1 connected'") == 0) {
            connected = true;
            break;
        }
        sleep_for(milliseconds(SCREEN_WAIT_MS));
    }

    // Skip quietly when this machine has no Waveshare panel
    if (!connected)
        return;

    // A rotate modeset blanks the panel and shows the NVIDIA logo in native landscape
    if (!display_is_rotated_left()) {
        int rotated = system("xrandr --output DP-1 --rotate left 2>/dev/null");
        (void)rotated;
        for (int try_index = 0; try_index < SCREEN_ROTATE_TRIES; try_index++) {
            if (display_is_rotated_left())
                break;
            rotated = system("xrandr --output DP-1 --rotate left 2>/dev/null");
            sleep_for(milliseconds(SCREEN_WAIT_MS));
        }
    }

    // Keep the touch device mapped to the rotated output
    int mapped = system("xinput map-to-output \"WaveShare WS170120\" DP-1 2>/dev/null");
    (void)mapped;
#endif
}

// True when DP-1 is already in the portrait left orientation
static bool display_is_rotated_left() {
    return system("xrandr --query 2>/dev/null | grep -q '^DP-1 connected.* left ('") == 0;
}

// Open the window and draw the face before slower startup work
static void show_face() {
    // Create the window, continue headless if the display is missing
    if (show_window && !create_window())
        show_window = false;

    // Build the face and point it straight ahead
    face = create_face();
    reset_face_animation(&face);

    // Pump a few frames so the compositor actually shows the eyes
    for (int frame = 0; frame < STARTUP_FRAMES && !g_quit; frame++) draw_face();
}

// Draw one face frame and give X a chance to map the window
static void draw_face() {
    if (!show_window || !renderer) {
        sleep_for(milliseconds(FRAME_MS));
        return;
    }

    // Deliver expose and map events, a blocked main thread leaves the window black
    SDL_PumpEvents();
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        if (event.type == SDL_QUIT)
            g_quit = true;
    }

    // Animate the face and keep the battery strip on the first frames
    check_battery();
    update_face_animation(&face);
    SDL_SetRenderDrawColor(renderer, BACKGROUND_COLOR.r, BACKGROUND_COLOR.g, BACKGROUND_COLOR.b, BACKGROUND_COLOR.a);
    SDL_RenderClear(renderer);
    vectorRenderer.render(renderer);
    draw_status_bar(battery_text().c_str(), face.font, call_overlay_open());
    SDL_RenderPresent(renderer);
    SDL_Delay(FRAME_MS);
}

// Open the servo bus on a worker so the face can keep drawing
static int start_servos() {
    atomic<bool> done{false};
    int result = 0;
    thread worker([&]() {
        result = open_servos();
        done = true;
    });

    // Keep drawing until the bus scan finishes
    while (!done && !g_quit)
        draw_face();
    worker.join();
    return result;
}

// Sweep the servos around center, then exit
static int sweep_servo_test(bool no_servos) {
    if (no_servos) {
        printf("Servos disabled, not sweeping\n");
        return 0;
    }
    sweep_servos();
    return 0;
}

// Fork and exec talk.py, unless one is already running
static bool start_talk_process() {
    // Leave an existing talk.py alone
    pid_t existing_pid = find_talk_pid();
    if (existing_pid > 0) {
        cout << "talk.py already running, pid " << existing_pid << endl;
        return true;
    }

    // Run the venv python against the script, both relative to the repo
    string talk_python = repo_path(TALK_PYTHON_FROM_REPO);
    string talk_script = repo_path(TALK_SCRIPT_FROM_REPO);
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork talk.py");
        return false;
    }
    if (pid == 0) {
        // Child, drop inherited fds above stdin, stdout, and stderr
        struct rlimit limit{};
        int max_descriptor = DEFAULT_MAX_DESCRIPTORS;
        if (getrlimit(RLIMIT_NOFILE, &limit) == 0 && limit.rlim_cur > 0 && limit.rlim_cur < MAX_SANE_DESCRIPTORS) {
            max_descriptor = static_cast<int>(limit.rlim_cur);
        }
        for (int descriptor = 3; descriptor < max_descriptor; descriptor++) close(descriptor);

        // Exec talk.py, and report it when the exec itself fails
        execl(talk_python.c_str(), talk_python.c_str(), talk_script.c_str(), static_cast<char*>(nullptr));
        cerr << "Error: talk.py: " << strerror(errno) << endl;
        _exit(TALK_EXEC_FAILED);
    }

    // Parent, remember the child so it can be reaped and stopped
    g_talk_pid = pid;
    cout << "Starting talk.py..." << endl;
    return true;
}

// Look through /proc for a python running talk.py
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
        ifstream input(entry.path() / "cmdline", ios::binary);
        string cmdline((istreambuf_iterator<char>(input)), istreambuf_iterator<char>());
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

// Turn a path inside the repo into an absolute one
static string repo_path(const char* relative) {
    error_code error;
    filesystem::path executable = filesystem::read_symlink("/proc/self/exe", error);
    if (error)
        return relative;

    // robot/build/robot sits two folders under the repo root
    filesystem::path repo = executable.parent_path().parent_path().parent_path();
    return (repo / relative).string();
}

// Pause camera and talk for other programs, or restore them after
static void apply_call_handoff(FaceTracker& faceTracker) {
    int command = take_call_handoff();
    if (command == CALL_HANDOFF_NONE)
        return;

    // Free the camera and the microphone for the call
    if (command == CALL_HANDOFF_PAUSE) {
        if (!g_call_paused) {
            faceTracker.stopCamera();
            g_call_had_talk = !g_no_talk && g_talk_pid > 0;
            if (g_call_had_talk)
                stop_talk_process();
            g_call_paused = true;
            cout << "Paused camera and talk." << endl;
        }
        complete_call_handoff(true);
        return;
    }

    // Take them back once the call ends
    if (g_call_paused) {
        if (use_camera && faceTracker.initializeCamera())
            faceTracker.startTracking();
        if (g_call_had_talk)
            start_talk_process();
        g_call_paused = false;
        g_call_had_talk = false;
        setStatus("");
        cout << "Resumed camera and talk." << endl;
    }
    complete_call_handoff(true);
}

// Collect the talk child once it exits, and say why it went
static void reap_talk_process() {
    if (g_talk_pid <= 0)
        return;
    int status = 0;
    pid_t waited = waitpid(g_talk_pid, &status, WNOHANG);
    if (waited <= 0)
        return;

    // A failed exec already printed its own error
    if (WIFEXITED(status)) {
        if (WEXITSTATUS(status) != TALK_EXEC_FAILED) {
            cout << "talk.py exited with code " << WEXITSTATUS(status) << endl;
        }
    } else if (WIFSIGNALED(status)) {
        cout << "talk.py killed by signal " << WTERMSIG(status) << endl;
    } else {
        cout << "talk.py exited" << endl;
    }
    g_talk_pid = -1;
}

// Stop child talk, sockets, tracking, then drop torque
static void stop_robot(FaceTracker& faceTracker) {
    g_quit = true;
    cout << "Quit" << endl;
    stop_talk_process();
    stop_interface();
    stop_servo_position_log();
    faceTracker.stopTracking();
    relax_servos();
    if (show_window)
        close_window();
}

// Ask talk.py to stop, then kill it if it lingers
static void stop_talk_process() {
    if (g_talk_pid <= 0)
        return;
    cout << "Stopping talk.py pid " << g_talk_pid << endl;
    kill(g_talk_pid, SIGTERM);

    // Poll for a clean exit
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

    // Out of patience
    kill(g_talk_pid, SIGKILL);
    waitpid(g_talk_pid, &status, WNOHANG);
    g_talk_pid = -1;
}
