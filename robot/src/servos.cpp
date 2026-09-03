// Drive servos

// Servo protocol and serial port
#include "servos.h"
#include "servos/SCSerial.h"
#include "servos/SMS_STS.h"
#include "config.h"
#include "face.h"

// Standard library
#include <cstdio>
#include <mutex>
#include <thread>
#include <atomic>
#include <chrono>
#include <cctype>
#include <string>

// Shorthand for names used below
using namespace std;
using namespace std::chrono;
using namespace std::this_thread;

// Servo bus IDs
const int SERVO_ID_PAN = 1;
const int SERVO_ID_TILT = 2;
const int SERVO_ID_HAT = 3;

// One servo, its travel limits, and commanded position
struct Servo {
    int id;
    const char *name;
    int position;
    int min_limit;
    int max_limit;
    bool found;
};

// Pan and tilt come first so leftover UART bytes do not land on hat
static Servo servos[] = {
    {SERVO_ID_PAN,  "pan",  400, 0,   800, false},
    {SERVO_ID_TILT, "tilt", 500, 200, 800, false},
    {SERVO_ID_HAT,  "hat",  400, 0,   800, false},
};

// Serialize servo writes
static recursive_mutex servo_mutex;

// On-screen face yaw for a left or right look
const float LOOK_FACE_PAN = 20.0f;

// Servo parameters
int speed = 1800;
int acceleration = 20;

// Sweep around center
int sweep_nudge_percent = 20;
int sweep_range_percent = 80;
int sweep_step_ms = 2000;
int sweep_poll_ms = 50;
int sweep_arrival_amount = 10;
int sweep_speed = 600;
int sweep_acceleration = 10;

// Bus addressing and detection budget
const int SERVO_BROADCAST_ID = 0;
const int SERVO_BUS_BROADCAST_ID = 254;
const int SERVO_ID_MIN = 1;
const int SERVO_ID_MAX = 253;
const int SERVO_SWEEP_SCAN_MAX = 20;
const int SERVO_POSITION_LOG_MS = 1000;
const int SERVO_DETECT_ATTEMPTS = 2;
const int SERVO_DETECT_TIMEOUT_MS = 50;
const int SERVO_DETECT_GAP_MS = 20;
const int SERVO_BAUD_SETTLE_MS = 100;
const int SERVO_NAME_WIDTH = 4;
const int SWEEP_LABEL_SIZE = 32;
const int SWEEP_COMMAND_COLUMN = 14;

// Skip writes and log position with --no-servos
bool servos_enabled = true;
static atomic<bool> servo_position_log_running{false};
static thread servo_position_log_thread;

// Serial port, USB adapter or onboard UART
string port_name = servo_port_name();
SerialPort serial(port_name);
SMS_STS st;

// Main quit flag, set by Ctrl-C
extern volatile bool g_quit;

// Probe one servo by position reads, then a ping
static bool detect_servo(Servo &servo);
static void probe_known_servos();
static bool open_first_servo_port();
static Servo *known_servo(int id);
static bool detect_or_promote_servo(Servo &servo);
static void scan_sweep_ids();

// Extra IDs found while --servos scans 1 to 20
static Servo sweep_scan_servos[SERVO_SWEEP_SCAN_MAX];
static char sweep_scan_names[SERVO_SWEEP_SCAN_MAX][8];
static int sweep_scan_count = 0;

// Put min below max when config.json has them reversed
static void swap_inverted_limits(Servo &servo) {
    if (servo.min_limit <= servo.max_limit) return;
    printf("Servo ID %d %s config min %d is above max %d, swapping\n", servo.id, servo.name, servo.min_limit, servo.max_limit);
    fflush(stdout);
    int high = servo.min_limit;
    servo.min_limit = servo.max_limit;
    servo.max_limit = high;
}

// Detect pan, tilt, and hat at 1 Mbps, then promote leftover 115200 motors
static void probe_known_servos() {
    // Find each servo at 1 Mbps
    for (Servo &servo : servos)
        servo.found = detect_servo(servo);

    // Promote leftover 115200 servos to 1 Mbps
    bool missing = false;
    for (Servo &servo : servos) {
        if (!servo.found) missing = true;
    }
    if (!missing) return;

    // Probe leftover servos at 115200
    serial.setBaudRate(SERVO_BAUD_RATE_115200);
    bool promoted = false;
    for (Servo &servo : servos) {
        if (servo.found) continue;
        if (st.ReadPos(servo.id) == -1 && st.Ping(servo.id) == -1) continue;
        printf("Servo ID %d %-*s answered at %d baud, moving it to %d\n", servo.id, SERVO_NAME_WIDTH, servo.name, SERVO_BAUD_RATE_115200, SERVO_BAUD_RATE);
        fflush(stdout);
        st.unLockEprom(servo.id);
        st.writeByte(servo.id, SMS_STS_BAUD_RATE, _1M);
        st.LockEprom(servo.id);
        promoted = true;
    }
    serial.setBaudRate(SERVO_BAUD_RATE);
    sleep_for(milliseconds(SERVO_BAUD_SETTLE_MS));
    if (!promoted) return;
    for (Servo &servo : servos) {
        if (!servo.found) servo.found = detect_servo(servo);
    }
}

// Open the first USB or onboard port that exists
static bool open_first_servo_port() {
    if (serial.isOpen()) {
        st.pSerial = &serial;
        return true;
    }
    for (int index = 0; index < SERVO_PORT_CANDIDATE_COUNT; index++) {
        const char *path = SERVO_PORT_CANDIDATES[index];
        if (access(path, F_OK) != 0) continue;
        serial.setPort(path);
        port_name = path;
        if (!serial.openPort()) continue;
        st.pSerial = &serial;
        serial.setBaudRate(SERVO_BAUD_RATE);
        st.IOTimeOut = SERVO_DETECT_TIMEOUT_MS;
        return true;
    }
    return false;
}

int open_servos() {
    // Load servo travel limits from config.json
    AppConfig config = loadConfig();
    servos[0].min_limit = config.pan_min;
    servos[0].max_limit = config.pan_max;
    servos[1].min_limit = config.tilt_min;
    servos[1].max_limit = config.tilt_max;
    servos[2].min_limit = config.hat_min;
    servos[2].max_limit = config.hat_max;
    for (Servo &servo : servos) {
        swap_inverted_limits(servo);
    }
    servos[0].position = servos[0].min_limit + (servos[0].max_limit - servos[0].min_limit) / 2;
    servos[1].position = servos[1].min_limit + (servos[1].max_limit - servos[1].min_limit) / 2;
    servos[2].position = servos[2].min_limit;

    // Try USB first, then the onboard UART, keep the first bus that answers
    st.IOTimeOut = SERVO_DETECT_TIMEOUT_MS;
    bool opened = false;
    for (int index = 0; index < SERVO_PORT_CANDIDATE_COUNT; index++) {
        const char *path = SERVO_PORT_CANDIDATES[index];
        if (access(path, F_OK) != 0) continue;
        serial.setPort(path);
        port_name = path;
        if (!serial.openPort()) continue;
        st.pSerial = &serial;
        if (!serial.setBaudRate(SERVO_BAUD_RATE)) {
            serial.closePort();
            continue;
        }
        opened = true;
        printf("Servo bus %d baud on %s\n", SERVO_BAUD_RATE, port_name.c_str());
        fflush(stdout);
        probe_known_servos();
        int answered = 0;
        for (Servo &servo : servos) {
            if (servo.found) answered++;
        }
        if (answered > 0) break;
        serial.closePort();
        st.pSerial = NULL;
    }
    if (!opened) return 1;

    // Name silent servos once, then fail only when none answered
    int found = 0;
    for (Servo &servo : servos) {
        if (servo.found) {
            found++;
            continue;
        }
        printf("Servo ID %d %-*s not responding\n", servo.id, SERVO_NAME_WIDTH, servo.name);
        fflush(stdout);
    }
    if (found == 0) {
        printf("Error: Servos: no response on %s, plugged in?\n", port_name.c_str());
        return 1;
    }

    // Torque is off after any previous relax, so turn it back on before parking
    for (Servo &servo : servos) {
        if (servo.found) st.EnableTorque(servo.id, 1);
    }
    servos_enabled = true;

    // Log the park pose
    const char *separator = "";
    for (Servo &servo : servos) {
        if (!servo.found) continue;
        printf("%s%c%s: %d", separator, toupper(static_cast<unsigned char>(servo.name[0])), servo.name + 1, servo.position);
        separator = ", ";
    }
    printf("\n");
    fflush(stdout);
    move_servos();
    return 0;
}

// Read position until the servo answers, a bare ping is only a last resort
static bool detect_servo(Servo &servo) {
    // Accept the first valid position reply
    for (int attempt = 0; attempt < SERVO_DETECT_ATTEMPTS; attempt++) {
        int position = st.ReadPos(servo.id);
        if (position != -1) {
            printf("Servo ID %d %-*s OK at position %d\n", servo.id, SERVO_NAME_WIDTH, servo.name, position);
            fflush(stdout);
            return true;
        }
        sleep_for(milliseconds(SERVO_DETECT_GAP_MS));
    }

    // Count a ping as present when every position read failed
    if (st.Ping(servo.id) == -1) return false;
    printf("Servo ID %d %-*s OK, answers ping but not position reads\n", servo.id, SERVO_NAME_WIDTH, servo.name);
    fflush(stdout);
    return true;
}

// Open the bus, turn torque off, and leave servos disabled
int relax_servos() {
    // Skip if already off
    if (!servos_enabled) return 0;
    servos_enabled = false;

    // Open USB or the onboard UART
    if (!open_first_servo_port()) {
        printf("Servos disabled, could not open a servo port to relax\n");
        return 0;
    }

    // Relax every servo on the bus, then the known IDs
    st.EnableTorque(SERVO_BUS_BROADCAST_ID, 0);
    for (Servo &servo : servos)
        st.EnableTorque(servo.id, 0);

    // Log that torque is off
    printf("Servos off\n");
    fflush(stdout);
    return 0;
}

// Read every servo and print one line
void print_servo_positions() {
    lock_guard<recursive_mutex> lock(servo_mutex);
    if (!st.pSerial) return;

    // Read first so the line is complete before printing
    string line = "Servos:";
    const char *separator = " ";
    bool first = true;
    for (Servo &servo : servos) {
        if (!first) sleep_for(milliseconds(SERVO_DETECT_GAP_MS));
        first = false;
        line += separator;
        line += servo.name;
        line += " ";
        line += to_string(st.ReadPos(servo.id));
        separator = ", ";
    }
    printf("%s\n", line.c_str());
    fflush(stdout);
}

// Print positions every second until stop_servo_position_log
void start_servo_position_log() {
    if (servo_position_log_running) return;
    servo_position_log_running = true;
    servo_position_log_thread = thread([]() {
        while (servo_position_log_running) {
            print_servo_positions();
            sleep_for(milliseconds(SERVO_POSITION_LOG_MS));
        }
    });
}

// Stop the position log thread
void stop_servo_position_log() {
    servo_position_log_running = false;
    if (servo_position_log_thread.joinable()) servo_position_log_thread.join();
}

// Keep a value inside a travel range
static int clamp_to_range(int value, int min_value, int max_value) {
    int low = min_value < max_value ? min_value : max_value;
    int high = min_value < max_value ? max_value : min_value;
    if (value < low) return low;
    if (value > high) return high;
    return value;
}

// Move servos
void move_servos() {
    lock_guard<recursive_mutex> lock(servo_mutex);
    if (!servos_enabled) return;
    if (!st.pSerial) {
       printf("No servos detected, not moving\n");
       return;
    }

    // Clamp and write only the servos that answered at open
    for (Servo &servo : servos) {
        servo.position = clamp_to_range(servo.position, servo.min_limit, servo.max_limit);
        if (servo.found) st.WritePosEx(servo.id, servo.position, speed, acceleration);
    }
}

// Find the named pan, tilt, or hat servo with this bus ID
static Servo *known_servo(int id) {
    for (Servo &servo : servos) {
        if (servo.id == id) return &servo;
    }
    return NULL;
}

// Detect at 1 Mbps, then try leftover 115200 and move that servo to 1 Mbps
static bool detect_or_promote_servo(Servo &servo) {
    if (detect_servo(servo)) return true;
    serial.setBaudRate(SERVO_BAUD_RATE_115200);
    if (st.ReadPos(servo.id) == -1 && st.Ping(servo.id) == -1) {
        serial.setBaudRate(SERVO_BAUD_RATE);
        return false;
    }
    printf("Servo ID %d %-*s answered at %d baud, moving it to %d\n", servo.id, SERVO_NAME_WIDTH, servo.name, SERVO_BAUD_RATE_115200, SERVO_BAUD_RATE);
    fflush(stdout);
    st.unLockEprom(servo.id);
    st.writeByte(servo.id, SMS_STS_BAUD_RATE, _1M);
    st.LockEprom(servo.id);
    serial.setBaudRate(SERVO_BAUD_RATE);
    sleep_for(milliseconds(SERVO_BAUD_SETTLE_MS));
    return detect_servo(servo);
}

// Probe IDs 1 to 20 so --servos finds more than pan, tilt, and hat
static void scan_sweep_ids() {
    sweep_scan_count = 0;
    for (int id = SERVO_ID_MIN; id <= SERVO_SWEEP_SCAN_MAX; id++) {
        Servo *known = known_servo(id);
        if (known) {
            if (known->found) sweep_scan_servos[sweep_scan_count++] = *known;
            continue;
        }

        // Extra IDs use the same default travel as an unconfigured hat
        snprintf(sweep_scan_names[id - 1], sizeof(sweep_scan_names[id - 1]), "id%d", id);
        Servo extra = {id, sweep_scan_names[id - 1], 400, 0, 800, false};
        extra.found = detect_or_promote_servo(extra);
        if (!extra.found) continue;
        extra.position = extra.min_limit + (extra.max_limit - extra.min_limit) / 2;
        st.EnableTorque(extra.id, 1);
        sweep_scan_servos[sweep_scan_count++] = extra;
    }
}

// Center each motor, nudge each servo, then the same at min and max
static void sweep_line(const char *label, Servo &servo, int position);
void sweep_servos() {
    char label[SWEEP_LABEL_SIZE];
    char center_label[SWEEP_LABEL_SIZE];
    scan_sweep_ids();
    Servo *list = sweep_scan_count > 0 ? sweep_scan_servos : servos;
    int count = sweep_scan_count > 0 ? sweep_scan_count : (int)(sizeof(servos) / sizeof(servos[0]));

    // Center one motor at a time
    for (int index = 0; index < count; index++) {
        Servo &servo = list[index];
        snprintf(label, sizeof(label), "%s center", servo.name);
        sweep_line(label, servo, servo.min_limit + (servo.max_limit - servo.min_limit) / 2);
    }

    // Nudge narrow then wide, returning to center between
    int percents[] = {sweep_nudge_percent, sweep_range_percent};
    for (int percent : percents) {
        for (int index = 0; index < count; index++) {
            Servo &servo = list[index];
            int center = servo.min_limit + (servo.max_limit - servo.min_limit) / 2;
            snprintf(center_label, sizeof(center_label), "%s center", servo.name);
            snprintf(label, sizeof(label), "%s %d%% min", servo.name, percent);
            sweep_line(label, servo, center + (servo.min_limit - center) * percent / 100);
            sweep_line(center_label, servo, center);
            snprintf(label, sizeof(label), "%s %d%% max", servo.name, percent);
            sweep_line(label, servo, center + (servo.max_limit - center) * percent / 100);
            sweep_line(center_label, servo, center);
        }
    }

    // Center, walk onto real limits, then park
    for (int index = 0; index < count; index++) {
        Servo &servo = list[index];
        snprintf(label, sizeof(label), "%s center", servo.name);
        sweep_line(label, servo, servo.min_limit + (servo.max_limit - servo.min_limit) / 2);
    }
    for (int index = 0; index < count; index++) {
        Servo &servo = list[index];
        int center = servo.min_limit + (servo.max_limit - servo.min_limit) / 2;
        snprintf(center_label, sizeof(center_label), "%s center", servo.name);
        snprintf(label, sizeof(label), "%s min", servo.name);
        sweep_line(label, servo, servo.min_limit);
        sweep_line(center_label, servo, center);
        snprintf(label, sizeof(label), "%s max", servo.name);
        sweep_line(label, servo, servo.max_limit);
        sweep_line(center_label, servo, center);
    }
    for (int index = 0; index < count; index++) {
        Servo &servo = list[index];
        snprintf(label, sizeof(label), "%s center", servo.name);
        sweep_line(label, servo, servo.min_limit + (servo.max_limit - servo.min_limit) / 2);
    }
    printf(g_quit ? "Sweep stopped\n" : "Sweep done\n");
}

// Read present position for one motor
static int read_present_position(Servo &servo) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    if (!st.pSerial) return -1;
    return st.ReadPos(servo.id);
}

// Print a sweep line, move one motor, then wait
static void sweep_line(const char *label, Servo &servo, int position) {
    if (g_quit || !servo.found) return;

    // Write only the motor for this step
    int command = clamp_to_range(position, servo.min_limit, servo.max_limit);
    {
        lock_guard<recursive_mutex> lock(servo_mutex);
        if (servos_enabled && st.pSerial) {
            servo.position = command;
            st.WritePosEx(servo.id, servo.position, sweep_speed, sweep_acceleration);
        } else if (servos_enabled) {
            printf("No servos detected, not moving\n");
        }
    }
    printf("%-*s command %d\n", SWEEP_COMMAND_COLUMN, label, command);
    fflush(stdout);

    // Wait before the next line, stop on Ctrl-C
    int waited_ms = 0;
    while (waited_ms < sweep_step_ms && !g_quit) {
        sleep_for(milliseconds(sweep_poll_ms));
        waited_ms += sweep_poll_ms;
    }

    // Only say when the motor did not get there
    int after = read_present_position(servo);
    int delta = after - command;
    if (delta < 0) delta = -delta;
    if (after == -1) {
        printf("%s failed, commanded %d, no position reply\n", label, command);
        fflush(stdout);
    } else if (delta > sweep_arrival_amount) {
        printf("%s failed, commanded %d got %d\n", label, command, after);
        fflush(stdout);
    }
}

// Move pan, tilt, and hat some relative amount
void move_head(int pan_diff, int tilt_diff, int hat_diff) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    servos[0].position += pan_diff;
    servos[1].position += tilt_diff;
    servos[2].position += hat_diff;
    move_servos();
}

// Move head to a specific direction
void move_head(const string &direction) {
    if      (direction == "Up")    move_head(  0,  200, 0);
    else if (direction == "Down")  move_head(  0, -200, 0);
    else if (direction == "Left")  move_head(-600,   0, 0);
    else if (direction == "Right") move_head( 600,   0, 0);
}

// Map -100..100 onto a servo min..max range
static int percent_to_servo(int percent, int min_value, int max_value) {
    percent = clamp_to_range(percent, -100, 100);
    int span = max_value - min_value;
    return min_value + (percent + 100) * span / 200;
}

// Map a servo position back onto -100..100
static int servo_to_percent(int position, int min_value, int max_value) {
    int span = max_value - min_value;
    if (span == 0) return 0;
    return -100 + ((position - min_value) * 200 + span / 2) / span;
}

// Face left, right, or center from the midpoint, not from the current pose
void look_head(const string &direction, double degrees) {
    lock_guard<recursive_mutex> lock(servo_mutex);

    // Clamp degrees onto 0-90, 90 is full pan travel
    if (degrees < 0) degrees = 0;
    if (degrees > 90) degrees = 90;
    int pan_percent = static_cast<int>((degrees / 90.0) * 100);

    // Set absolute pan from center, tilt stays centered
    servos[1].position = percent_to_servo(0, servos[1].min_limit, servos[1].max_limit);
    if (direction == "center") {
        servos[0].position = percent_to_servo(0, servos[0].min_limit, servos[0].max_limit);
        servos[2].position = servos[2].min_limit;
        face.lookTiltX = 0.0f;
        face.lookTiltY = 0.0f;
    } else if (direction == "left") {
        servos[0].position = percent_to_servo(-pan_percent, servos[0].min_limit, servos[0].max_limit);
        face.lookTiltX = LOOK_FACE_PAN * static_cast<float>(degrees / 90.0);
        face.lookTiltY = 0.0f;
    } else if (direction == "right") {
        servos[0].position = percent_to_servo(pan_percent, servos[0].min_limit, servos[0].max_limit);
        face.lookTiltX = -LOOK_FACE_PAN * static_cast<float>(degrees / 90.0);
        face.lookTiltY = 0.0f;
    } else {
        return;
    }

    // Write servos and keep the on-screen face matching
    move_servos();
    printf("Look %s to pan %d, tilt %d\n", direction.c_str(), servos[0].position, servos[1].position);
    fflush(stdout);
}

// Park pan and tilt at center, hat at min, all the way up
void center() {
    lock_guard<recursive_mutex> lock(servo_mutex);
    servos[0].position = servos[0].min_limit + (servos[0].max_limit - servos[0].min_limit) / 2;
    servos[1].position = servos[1].min_limit + (servos[1].max_limit - servos[1].min_limit) / 2;
    servos[2].position = servos[2].min_limit;
    move_servos();
}

// Current commanded pan, tilt, and hat
void get_head_position(int &pan, int &tilt, int &hat) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    pan = servos[0].position;
    tilt = servos[1].position;
    hat = servos[2].position;
}

// Set absolute pan, tilt, and hat
void set_head_position(int pan, int tilt, int hat) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    servos[0].position = pan;
    servos[1].position = tilt;
    servos[2].position = hat;
    move_servos();
}

// Current commanded pan, tilt, and hat as -100..100
void get_percent(int &pan, int &tilt, int &hat) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    pan = servo_to_percent(servos[0].position, servos[0].min_limit, servos[0].max_limit);
    tilt = servo_to_percent(servos[1].position, servos[1].min_limit, servos[1].max_limit);
    hat = servo_to_percent(servos[2].position, servos[2].min_limit, servos[2].max_limit);
}

// Set pan, tilt, and hat from percent commands
void set_percent(int pan, int tilt, int hat) {
    printf("Move head to pan %d, tilt %d, hat %d", pan, tilt, hat);
    for (Servo &servo : servos) {
        if (!servo.found) printf(", %s dead", servo.name);
    }
    printf("\n");
    fflush(stdout);
    set_head_position(percent_to_servo(pan, servos[0].min_limit, servos[0].max_limit), percent_to_servo(tilt, servos[1].min_limit, servos[1].max_limit), percent_to_servo(hat, servos[2].min_limit, servos[2].max_limit));
}

// Nudge pan, tilt, and hat in percent
void move_percent(int pan_diff, int tilt_diff, int hat_diff) {
    int pan = 0;
    int tilt = 0;
    int hat = 0;
    get_percent(pan, tilt, hat);
    set_percent(pan + pan_diff, tilt + tilt_diff, hat + hat_diff);
}

// Arrow keys and ijkluo nudge pan, tilt, and hat
void handle_servo_keyboard_input(SDL_Event* event, Face* face) {
    if (event->type != SDL_KEYDOWN) return;
    switch (event->key.keysym.sym) {
        case SDLK_UP:    move_head(0, 40, 0);     update_face(face, 0, 1); break;
        case SDLK_DOWN:  move_head(0, -40, 0);    update_face(face, 0, -1); break;
        case SDLK_RIGHT: move_head(-40, 0, 0);    break;
        case SDLK_LEFT:  move_head(40, 0, 0);     break;
        case SDLK_j:     move_head(900, 0, 0);    update_face(face, 5, 0); break;
        case SDLK_l:     move_head(-900, 0, 0);   update_face(face, -5, 0); break;
        case SDLK_i:     move_head(0, 200, 0);    update_face(face, 5, 0); break;
        case SDLK_k:     move_head(0, -200, 0);   update_face(face, -5, 0); break;
        case SDLK_u:     move_head(0, 0, 40);     break;
        case SDLK_o:     move_head(0, 0, -40);    break;
    }
}

// Change a servo ID in EEPROM, use 0 as old_id to address every servo
int set_servo_id(int old_id, int new_id) {
    if ((old_id != SERVO_BROADCAST_ID && (old_id < SERVO_ID_MIN || old_id > SERVO_ID_MAX)) || new_id < SERVO_ID_MIN || new_id > SERVO_ID_MAX) {
        printf("Error: Servo IDs must be %d-%d, or %d to address every servo on the bus\n", SERVO_ID_MIN, SERVO_ID_MAX, SERVO_BROADCAST_ID);
        return 1;
    }

    // Open USB first, then onboard, until this ID answers
    st.IOTimeOut = SERVO_DETECT_TIMEOUT_MS;
    bool opened = false;
    for (int index = 0; index < SERVO_PORT_CANDIDATE_COUNT; index++) {
        const char *path = SERVO_PORT_CANDIDATES[index];
        if (access(path, F_OK) != 0) continue;
        serial.setPort(path);
        port_name = path;
        if (!serial.openPort()) continue;
        st.pSerial = &serial;
        serial.setBaudRate(SERVO_BAUD_RATE);
        if (old_id == SERVO_BROADCAST_ID || st.ReadPos(old_id) != -1 || st.Ping(old_id) != -1) {
            opened = true;
            break;
        }
        serial.closePort();
        st.pSerial = NULL;
    }
    if (!opened) {
        printf("Error: Could not open a servo port for ID %d\n", old_id);
        return 1;
    }

    // Feetech broadcast on the wire is 254
    int bus_id = old_id;
    if (old_id == SERVO_BROADCAST_ID) {
        bus_id = SERVO_BUS_BROADCAST_ID;
        printf("Setting ID to %d on %s, only one servo should be plugged in\n", new_id, port_name.c_str());
    } else {
        printf("Setting servo %d to ID %d on %s\n", old_id, new_id, port_name.c_str());
    }

    // Unlock, write the new ID, lock again
    if (!st.unLockEprom(bus_id)) {
        printf("Error: Could not unlock servo %d\n", old_id);
        return 1;
    }
    if (!st.writeByte(bus_id, SMS_STS_ID, new_id)) {
        printf("Error: Could not write ID %d\n", new_id);
        st.LockEprom(bus_id);
        return 1;
    }
    st.LockEprom(new_id);

    // Confirm the new ID answers
    if (st.Ping(new_id) == -1) {
        printf("Error: Servo did not answer as ID %d\n", new_id);
        return 1;
    }
    printf("Servo ID is now %d\n", new_id);
    return 0;
}
