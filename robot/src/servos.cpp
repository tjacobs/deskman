// Drive servos

// Local
#include "servos.h"
#include "config.h"
#include "face.h"

// Servo protocol and serial port
#include "servos/SCSerial.h"
#include "servos/SMS_STS.h"

// System
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <mutex>
#include <string>
#include <thread>

// Shorthand for names used below
using namespace std;
using namespace std::chrono;
using namespace std::this_thread;

// Servo bus IDs
static const int SERVO_ID_PAN = 1;
static const int SERVO_ID_TILT = 2;
static const int SERVO_ID_HAT = 3;

// Bus addressing, the broadcast ID here maps to a different one on the wire
static const int SERVO_BROADCAST_ID = 0;
static const int SERVO_BUS_BROADCAST_ID = 254;
static const int SERVO_ID_MIN = 1;
static const int SERVO_ID_MAX = 253;

// How hard detection tries before calling a servo silent
static const int SERVO_DETECT_ATTEMPTS = 2;
static const int SERVO_DETECT_TIMEOUT_MS = 50;
static const int SERVO_DETECT_GAP_MS = 20;
static const int SERVO_BAUD_SETTLE_MS = 100;

// Pad servo names into a column, and print positions on a timer
static const int SERVO_NAME_WIDTH = 4;
static const int SERVO_POSITION_LOG_MS = 1000;

// Travel an unconfigured servo gets, matching pan
static const int SERVO_DEFAULT_POSITION = 400;
static const int SERVO_DEFAULT_MIN = 0;
static const int SERVO_DEFAULT_MAX = 800;
static const int SERVO_DEFAULT_DEGREES_MIN = -90;
static const int SERVO_DEFAULT_DEGREES_HIGH = 90;

// How fast the head moves while following a face
static const int SERVO_SPEED = 1800;
static const int SERVO_ACCELERATION = 20;

// Sweep around center, narrow first then wide, and slower than normal
static const int SWEEP_NUDGE_PERCENT = 20;
static const int SWEEP_RANGE_PERCENT = 80;
static const int SWEEP_STEP_MS = 2000;
static const int SWEEP_POLL_MS = 50;
static const int SWEEP_ARRIVAL_AMOUNT = 10;
static const int SWEEP_SPEED = 600;
static const int SWEEP_ACCELERATION = 10;

// How far the sweep scans for extra servos, and room for their labels
static const int SERVO_SWEEP_SCAN_MAX = 20;
static const int SWEEP_SCAN_NAME_SIZE = 8;
static const int SWEEP_LABEL_SIZE = 32;
static const int SWEEP_COMMAND_COLUMN = 14;

// A whole range, and the pan angle a full look uses
static const int PERCENT_FULL = 100;
static const float PAN_FULL_DEGREES = 90.0f;

// On-screen face yaw for a left or right look
static const float LOOK_FACE_PAN = 20.0f;

// Keyboard nudges in degrees, and how far the face looks along with them
static const int HEAD_KEY_NUDGE_DEGREES = 4;
static const int HEAD_KEY_PAN_DEGREES = 20;
static const int HEAD_KEY_TILT_DEGREES = 8;
static const int HEAD_KEY_HAT_DEGREES = 8;
static const int HEAD_KEY_FACE_LOOK = 5;

// One servo, count limits, degree labels, and commanded position
struct Servo {
    int id;
    const char *name;
    int position;
    int min_limit;
    int max_limit;
    int degrees_min;
    int degrees_high;
    bool found;
};

// Pan, tilt, and hat, overwritten by config.json at open
static Servo servos[] = {
    {SERVO_ID_PAN,  "pan",  400, 0,   800, -90, 90, false},
    {SERVO_ID_TILT, "tilt", 500, 200, 800, -10, 30, false},
    {SERVO_ID_HAT,  "hat",  400, 0,   800, -35, 90, false},
};

// Extra IDs found while the sweep scans the low bus IDs
static Servo sweep_scan_servos[SERVO_SWEEP_SCAN_MAX];
static char sweep_scan_names[SERVO_SWEEP_SCAN_MAX][SWEEP_SCAN_NAME_SIZE];
static int sweep_scan_count = 0;

// Serialize servo writes
static recursive_mutex servo_mutex;

// Skip writes and log position with --no-servos
static bool servos_enabled = true;
static atomic<bool> servo_position_log_running{false};
static thread servo_position_log_thread;

// Serial port, USB adapter or onboard UART
static string port_name = servo_port_name();
static SerialPort serial(port_name);
static SMS_STS servo_bus;

// Main quit flag, set by Ctrl-C
extern volatile bool g_quit;

// Later in this file
static void swap_inverted_limits(Servo &servo);
static int degrees_to_servo(const Servo &servo, float degrees);
static void probe_known_servos();
static bool detect_servo(Servo &servo);
static int clamp_to_range(int value, int min_value, int max_value);
static bool open_first_servo_port();
void print_servo_positions();
static void scan_sweep_ids();
static Servo *known_servo(int id);
static bool detect_or_promote_servo(Servo &servo);
static void sweep_line(const char *label, Servo &servo, int position);
static int read_present_position(Servo &servo);
static int servo_center(const Servo &servo);
static float servo_to_degrees(const Servo &servo, int position);
static int degrees_to_counts(const Servo &servo, float degrees);
static float counts_to_degrees(const Servo &servo, float counts);

int open_servos() {
    // Load servo travel limits from config.json
    AppConfig config = loadConfig();
    servos[0].min_limit = config.pan_min;
    servos[0].max_limit = config.pan_max;
    servos[1].min_limit = config.tilt_min;
    servos[1].max_limit = config.tilt_max;
    servos[2].min_limit = config.hat_min;
    servos[2].max_limit = config.hat_max;

    // Put any reversed pair the right way round
    for (Servo &servo : servos) {
        swap_inverted_limits(servo);
    }

    // Park pan and tilt facing forward, and the hat all the way down
    servos[0].position = degrees_to_servo(servos[0], 0);
    servos[1].position = degrees_to_servo(servos[1], 0);
    servos[2].position = degrees_to_servo(servos[2], servos[2].degrees_min);

    // Try USB first, then the onboard UART, keep the first bus that answers
    servo_bus.IOTimeOut = SERVO_DETECT_TIMEOUT_MS;
    bool opened = false;
    for (int index = 0; index < SERVO_PORT_CANDIDATE_COUNT; index++) {
        const char *path = SERVO_PORT_CANDIDATES[index];
        if (access(path, F_OK) != 0)
            continue;
        serial.setPort(path);
        port_name = path;
        if (!serial.openPort())
            continue;
        servo_bus.pSerial = &serial;
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
            if (servo.found)
                answered++;
        }
        if (answered > 0)
            break;
        serial.closePort();
        servo_bus.pSerial = NULL;
    }

    // No port answered at all
    if (!opened)
        return 1;

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
        if (servo.found)
            servo_bus.EnableTorque(servo.id, 1);
    }
    servos_enabled = true;

    // Log the park pose
    const char *separator = "";
    for (Servo &servo : servos) {
        if (!servo.found)
            continue;
        printf("%s%c%s: %d", separator, toupper(static_cast<unsigned char>(servo.name[0])), servo.name + 1, servo.position);
        separator = ", ";
    }
    printf("\n");
    fflush(stdout);
    move_servos();
    return 0;
}

// Put min below max when config.json has them reversed
static void swap_inverted_limits(Servo &servo) {
    if (servo.min_limit <= servo.max_limit)
        return;
    printf("Servo ID %d %s config min %d is above max %d, swapping\n", servo.id, servo.name, servo.min_limit, servo.max_limit);
    fflush(stdout);

    // Swap the counts and the degrees that go with them
    int high = servo.min_limit;
    servo.min_limit = servo.max_limit;
    servo.max_limit = high;
    int degrees_high = servo.degrees_min;
    servo.degrees_min = servo.degrees_high;
    servo.degrees_high = degrees_high;
}

// Map an axis degree onto its config.json count range
static int degrees_to_servo(const Servo &servo, float degrees) {
    if (degrees < servo.degrees_min)
        degrees = servo.degrees_min;
    if (degrees > servo.degrees_high)
        degrees = servo.degrees_high;

    // Scale the angle across the count range, guarding a zero span
    int servo_span = servo.max_limit - servo.min_limit;
    int degrees_span = servo.degrees_high - servo.degrees_min;
    if (degrees_span == 0)
        return servo.min_limit;
    return servo.min_limit + (int)lround((degrees - servo.degrees_min) * servo_span / degrees_span);
}

// Detect pan, tilt, and hat at the fast baud, then promote any left on the slow one
static void probe_known_servos() {
    // Find each servo at the fast baud
    for (Servo &servo : servos)
        servo.found = detect_servo(servo);

    // Nothing to promote when every servo answered
    bool missing = false;
    for (Servo &servo : servos) {
        if (!servo.found)
            missing = true;
    }
    if (!missing)
        return;

    // Probe the leftovers at the slow baud, and move any that answer
    serial.setBaudRate(SERVO_BAUD_RATE_115200);
    bool promoted = false;
    for (Servo &servo : servos) {
        if (servo.found)
            continue;
        if (servo_bus.ReadPos(servo.id) == -1 && servo_bus.Ping(servo.id) == -1)
            continue;
        printf("Servo ID %d %-*s answered at %d baud, moving it to %d\n", servo.id, SERVO_NAME_WIDTH, servo.name, SERVO_BAUD_RATE_115200, SERVO_BAUD_RATE);
        fflush(stdout);
        servo_bus.unLockEprom(servo.id);
        servo_bus.writeByte(servo.id, SMS_STS_BAUD_RATE, _1M);
        servo_bus.LockEprom(servo.id);
        promoted = true;
    }
    serial.setBaudRate(SERVO_BAUD_RATE);
    sleep_for(milliseconds(SERVO_BAUD_SETTLE_MS));
    if (!promoted)
        return;

    // Look again for the ones just moved across
    for (Servo &servo : servos) {
        if (!servo.found)
            servo.found = detect_servo(servo);
    }
}

// Read position until the servo answers, a bare ping is only a last resort
static bool detect_servo(Servo &servo) {
    // Accept the first valid position reply
    for (int attempt = 0; attempt < SERVO_DETECT_ATTEMPTS; attempt++) {
        int position = servo_bus.ReadPos(servo.id);
        if (position != -1) {
            printf("Servo ID %d %-*s OK at position %d\n", servo.id, SERVO_NAME_WIDTH, servo.name, position);
            fflush(stdout);
            return true;
        }
        sleep_for(milliseconds(SERVO_DETECT_GAP_MS));
    }

    // Count a ping as present when every position read failed
    if (servo_bus.Ping(servo.id) == -1)
        return false;
    printf("Servo ID %d %-*s OK, answers ping but not position reads\n", servo.id, SERVO_NAME_WIDTH, servo.name);
    fflush(stdout);
    return true;
}

// Write the commanded position of every servo out to the bus
void move_servos() {
    lock_guard<recursive_mutex> lock(servo_mutex);

    // Nothing to write with --no-servos, or with no bus open
    if (!servos_enabled)
        return;
    if (!servo_bus.pSerial) {
        printf("No servos detected, not moving\n");
        return;
    }

    // Clamp and write only the servos that answered at open
    for (Servo &servo : servos) {
        servo.position = clamp_to_range(servo.position, servo.min_limit, servo.max_limit);
        if (servo.found)
            servo_bus.WritePosEx(servo.id, servo.position, SERVO_SPEED, SERVO_ACCELERATION);
    }
}

// Keep a value inside a travel range, whichever way round it is given
static int clamp_to_range(int value, int min_value, int max_value) {
    int low = min_value < max_value ? min_value : max_value;
    int high = min_value < max_value ? max_value : min_value;
    if (value < low)
        return low;
    if (value > high)
        return high;
    return value;
}

// Open the bus, turn torque off, and leave servos disabled
int relax_servos() {
    // Skip if already off
    if (!servos_enabled)
        return 0;
    servos_enabled = false;

    // Open USB or the onboard UART
    if (!open_first_servo_port()) {
        printf("Servos disabled, could not open a servo port to relax\n");
        return 0;
    }

    // Relax every servo on the bus, then the known IDs
    servo_bus.EnableTorque(SERVO_BUS_BROADCAST_ID, 0);
    for (Servo &servo : servos)
        servo_bus.EnableTorque(servo.id, 0);

    // Log that torque is off
    printf("Servos off\n");
    fflush(stdout);
    return 0;
}

// Open the first USB or onboard port that exists
static bool open_first_servo_port() {
    // Reuse the port when one is already open
    if (serial.isOpen()) {
        servo_bus.pSerial = &serial;
        return true;
    }

    // Take the first candidate path that opens
    for (int index = 0; index < SERVO_PORT_CANDIDATE_COUNT; index++) {
        const char *path = SERVO_PORT_CANDIDATES[index];
        if (access(path, F_OK) != 0)
            continue;
        serial.setPort(path);
        port_name = path;
        if (!serial.openPort())
            continue;
        servo_bus.pSerial = &serial;
        serial.setBaudRate(SERVO_BAUD_RATE);
        servo_bus.IOTimeOut = SERVO_DETECT_TIMEOUT_MS;
        return true;
    }
    return false;
}

// Print positions on a timer until stop_servo_position_log
void start_servo_position_log() {
    if (servo_position_log_running)
        return;
    servo_position_log_running = true;
    servo_position_log_thread = thread([]() {
        while (servo_position_log_running) {
            print_servo_positions();
            sleep_for(milliseconds(SERVO_POSITION_LOG_MS));
        }
    });
}

// Read every servo and print one line
void print_servo_positions() {
    lock_guard<recursive_mutex> lock(servo_mutex);
    if (!servo_bus.pSerial)
        return;

    // Read first so the line is complete before printing
    string line = "Servos:";
    const char *separator = " ";
    bool first = true;
    for (Servo &servo : servos) {
        if (!first)
            sleep_for(milliseconds(SERVO_DETECT_GAP_MS));
        first = false;
        line += separator;
        line += servo.name;
        line += " ";
        line += to_string(servo_bus.ReadPos(servo.id));
        separator = ", ";
    }
    printf("%s\n", line.c_str());
    fflush(stdout);
}

// Stop the position log thread
void stop_servo_position_log() {
    servo_position_log_running = false;
    if (servo_position_log_thread.joinable())
        servo_position_log_thread.join();
}

// Center each motor, nudge each servo, then the same at min and max
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
        sweep_line(label, servo, servo_center(servo));
    }

    // Nudge narrow then wide, returning to center between
    int percents[] = {SWEEP_NUDGE_PERCENT, SWEEP_RANGE_PERCENT};
    for (int percent : percents) {
        for (int index = 0; index < count; index++) {
            Servo &servo = list[index];
            int center = servo_center(servo);
            snprintf(center_label, sizeof(center_label), "%s center", servo.name);
            snprintf(label, sizeof(label), "%s %d%% min", servo.name, percent);
            sweep_line(label, servo, center + (servo.min_limit - center) * percent / PERCENT_FULL);
            sweep_line(center_label, servo, center);
            snprintf(label, sizeof(label), "%s %d%% max", servo.name, percent);
            sweep_line(label, servo, center + (servo.max_limit - center) * percent / PERCENT_FULL);
            sweep_line(center_label, servo, center);
        }
    }

    // Center, walk onto real limits, then park
    for (int index = 0; index < count; index++) {
        Servo &servo = list[index];
        snprintf(label, sizeof(label), "%s center", servo.name);
        sweep_line(label, servo, servo_center(servo));
    }
    for (int index = 0; index < count; index++) {
        Servo &servo = list[index];
        int center = servo_center(servo);
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
        sweep_line(label, servo, servo_center(servo));
    }
    printf(g_quit ? "Sweep stopped\n" : "Sweep done\n");
}

// Probe the low bus IDs so --servos finds more than pan, tilt, and hat
static void scan_sweep_ids() {
    sweep_scan_count = 0;
    for (int id = SERVO_ID_MIN; id <= SERVO_SWEEP_SCAN_MAX; id++) {
        Servo *known = known_servo(id);
        if (known) {
            if (known->found)
                sweep_scan_servos[sweep_scan_count++] = *known;
            continue;
        }

        // Extra IDs use the same default travel as pan
        snprintf(sweep_scan_names[id - 1], sizeof(sweep_scan_names[id - 1]), "id%d", id);
        Servo extra = {id, sweep_scan_names[id - 1], SERVO_DEFAULT_POSITION, SERVO_DEFAULT_MIN, SERVO_DEFAULT_MAX, SERVO_DEFAULT_DEGREES_MIN, SERVO_DEFAULT_DEGREES_HIGH, false};
        extra.found = detect_or_promote_servo(extra);
        if (!extra.found)
            continue;
        extra.position = servo_center(extra);
        servo_bus.EnableTorque(extra.id, 1);
        sweep_scan_servos[sweep_scan_count++] = extra;
    }
}

// Middle of a servo's travel
static int servo_center(const Servo &servo) {
    return servo.min_limit + (servo.max_limit - servo.min_limit) / 2;
}

// Find the named pan, tilt, or hat servo with this bus ID
static Servo *known_servo(int id) {
    for (Servo &servo : servos) {
        if (servo.id == id)
            return &servo;
    }
    return NULL;
}

// Detect at the fast baud, then try the slow one and move that servo across
static bool detect_or_promote_servo(Servo &servo) {
    if (detect_servo(servo))
        return true;

    // Give up when it is silent at the slow baud too
    serial.setBaudRate(SERVO_BAUD_RATE_115200);
    if (servo_bus.ReadPos(servo.id) == -1 && servo_bus.Ping(servo.id) == -1) {
        serial.setBaudRate(SERVO_BAUD_RATE);
        return false;
    }

    // Rewrite its baud in EEPROM, then look for it again
    printf("Servo ID %d %-*s answered at %d baud, moving it to %d\n", servo.id, SERVO_NAME_WIDTH, servo.name, SERVO_BAUD_RATE_115200, SERVO_BAUD_RATE);
    fflush(stdout);
    servo_bus.unLockEprom(servo.id);
    servo_bus.writeByte(servo.id, SMS_STS_BAUD_RATE, _1M);
    servo_bus.LockEprom(servo.id);
    serial.setBaudRate(SERVO_BAUD_RATE);
    sleep_for(milliseconds(SERVO_BAUD_SETTLE_MS));
    return detect_servo(servo);
}

// Print a sweep line, move one motor, then wait
static void sweep_line(const char *label, Servo &servo, int position) {
    if (g_quit || !servo.found)
        return;

    // Write only the motor for this step
    int command = clamp_to_range(position, servo.min_limit, servo.max_limit);
    {
        lock_guard<recursive_mutex> lock(servo_mutex);
        if (servos_enabled && servo_bus.pSerial) {
            servo.position = command;
            servo_bus.WritePosEx(servo.id, servo.position, SWEEP_SPEED, SWEEP_ACCELERATION);
        } else if (servos_enabled) {
            printf("No servos detected, not moving\n");
        }
    }

    // Say what was asked for, lined up in a column
    printf("%-*s command %d\n", SWEEP_COMMAND_COLUMN, label, command);
    fflush(stdout);

    // Wait before the next line, stop on Ctrl-C
    int waited_ms = 0;
    while (waited_ms < SWEEP_STEP_MS && !g_quit) {
        sleep_for(milliseconds(SWEEP_POLL_MS));
        waited_ms += SWEEP_POLL_MS;
    }

    // Only say when the motor did not get there
    int after = read_present_position(servo);
    int delta = after - command;
    if (delta < 0)
        delta = -delta;
    if (after == -1) {
        printf("%s failed, commanded %d, no position reply\n", label, command);
        fflush(stdout);
    } else if (delta > SWEEP_ARRIVAL_AMOUNT) {
        printf("%s failed, commanded %d got %d\n", label, command, after);
        fflush(stdout);
    }
}

// Read present position for one motor
static int read_present_position(Servo &servo) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    if (!servo_bus.pSerial)
        return -1;
    return servo_bus.ReadPos(servo.id);
}

// Change a servo ID in EEPROM, use 0 as old_id to address every servo
int set_servo_id(int old_id, int new_id) {
    // Refuse an ID the bus cannot carry
    if ((old_id != SERVO_BROADCAST_ID && (old_id < SERVO_ID_MIN || old_id > SERVO_ID_MAX)) || new_id < SERVO_ID_MIN || new_id > SERVO_ID_MAX) {
        printf("Error: Servo IDs must be %d-%d, or %d to address every servo on the bus\n", SERVO_ID_MIN, SERVO_ID_MAX, SERVO_BROADCAST_ID);
        return 1;
    }

    // Open USB first, then onboard, until this ID answers
    servo_bus.IOTimeOut = SERVO_DETECT_TIMEOUT_MS;
    bool opened = false;
    for (int index = 0; index < SERVO_PORT_CANDIDATE_COUNT; index++) {
        const char *path = SERVO_PORT_CANDIDATES[index];
        if (access(path, F_OK) != 0)
            continue;
        serial.setPort(path);
        port_name = path;
        if (!serial.openPort())
            continue;
        servo_bus.pSerial = &serial;
        serial.setBaudRate(SERVO_BAUD_RATE);
        if (old_id == SERVO_BROADCAST_ID || servo_bus.ReadPos(old_id) != -1 || servo_bus.Ping(old_id) != -1) {
            opened = true;
            break;
        }
        serial.closePort();
        servo_bus.pSerial = NULL;
    }
    if (!opened) {
        printf("Error: Could not open a servo port for ID %d\n", old_id);
        return 1;
    }

    // Feetech uses its own broadcast ID on the wire
    int bus_id = old_id;
    if (old_id == SERVO_BROADCAST_ID) {
        bus_id = SERVO_BUS_BROADCAST_ID;
        printf("Setting ID to %d on %s, only one servo should be plugged in\n", new_id, port_name.c_str());
    } else {
        printf("Setting servo %d to ID %d on %s\n", old_id, new_id, port_name.c_str());
    }

    // Unlock, write the new ID, lock again
    if (!servo_bus.unLockEprom(bus_id)) {
        printf("Error: Could not unlock servo %d\n", old_id);
        return 1;
    }
    if (!servo_bus.writeByte(bus_id, SMS_STS_ID, new_id)) {
        printf("Error: Could not write ID %d\n", new_id);
        servo_bus.LockEprom(bus_id);
        return 1;
    }
    servo_bus.LockEprom(new_id);

    // Confirm the new ID answers
    if (servo_bus.Ping(new_id) == -1) {
        printf("Error: Servo did not answer as ID %d\n", new_id);
        return 1;
    }
    printf("Servo ID is now %d\n", new_id);
    return 0;
}

// Current commanded pan, tilt, and hat in degrees
void get_degrees(int &pan, int &tilt, int &hat) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    pan = (int)lround(servo_to_degrees(servos[0], servos[0].position));
    tilt = (int)lround(servo_to_degrees(servos[1], servos[1].position));
    hat = (int)lround(servo_to_degrees(servos[2], servos[2].position));
}

// Map a servo count back onto that axis degree range
static float servo_to_degrees(const Servo &servo, int position) {
    int servo_span = servo.max_limit - servo.min_limit;
    if (servo_span == 0)
        return servo.degrees_min;
    int degrees_span = servo.degrees_high - servo.degrees_min;
    return servo.degrees_min + (float)(position - servo.min_limit) * degrees_span / servo_span;
}

// Set pan, tilt, and hat from degrees, clamp to each axis range
void set_degrees(int pan, int tilt, int hat) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    servos[0].position = degrees_to_servo(servos[0], pan);
    servos[1].position = degrees_to_servo(servos[1], tilt);
    servos[2].position = degrees_to_servo(servos[2], hat);

    // Read the angles back, they may have been clamped
    pan = (int)lround(servo_to_degrees(servos[0], servos[0].position));
    tilt = (int)lround(servo_to_degrees(servos[1], servos[1].position));
    hat = (int)lround(servo_to_degrees(servos[2], servos[2].position));

    // Lean the drawn face the same way the head turned
    face.lookTiltX = -LOOK_FACE_PAN * static_cast<float>(pan) / PAN_FULL_DEGREES;
    face.lookTiltY = 0.0f;
    printf("Move head to pan %d, tilt %d, hat %d\n", pan, tilt, hat);
    fflush(stdout);
    move_servos();
}

// Add pan, tilt, and hat by converting each degree nudge into STS counts
void move_degrees(float pan_delta, float tilt_delta, float hat_delta) {
    lock_guard<recursive_mutex> lock(servo_mutex);
    servos[0].position += degrees_to_counts(servos[0], pan_delta);
    servos[1].position += degrees_to_counts(servos[1], tilt_delta);
    servos[2].position += degrees_to_counts(servos[2], hat_delta);
    move_servos();
}

// Turn a degree nudge into STS counts on that servo's mapped range
static int degrees_to_counts(const Servo &servo, float degrees) {
    int degrees_span = servo.degrees_high - servo.degrees_min;
    if (degrees_span == 0)
        return 0;
    int servo_span = servo.max_limit - servo.min_limit;
    return (int)lround(degrees * servo_span / (float)degrees_span);
}

// Pan degrees that match this many STS counts
float pan_degrees_from_counts(float counts) {
    return counts_to_degrees(servos[0], counts);
}

// Tilt degrees that match this many STS counts
float tilt_degrees_from_counts(float counts) {
    return counts_to_degrees(servos[1], counts);
}

// Turn a count nudge into degrees on that servo's mapped range
static float counts_to_degrees(const Servo &servo, float counts) {
    int servo_span = servo.max_limit - servo.min_limit;
    if (servo_span == 0)
        return 0;
    int degrees_span = servo.degrees_high - servo.degrees_min;
    return counts * (float)degrees_span / servo_span;
}

// Arrow keys and ijkluo nudge pan, tilt, and hat
void handle_servo_keyboard_input(SDL_Event* event, Face* face) {
    if (event->type != SDL_KEYDOWN)
        return;
    switch (event->key.keysym.sym) {
        case SDLK_UP:    move_degrees(0, HEAD_KEY_NUDGE_DEGREES, 0);     update_face(face, 0, 1); break;
        case SDLK_DOWN:  move_degrees(0, -HEAD_KEY_NUDGE_DEGREES, 0);    update_face(face, 0, -1); break;
        case SDLK_RIGHT: move_degrees(HEAD_KEY_NUDGE_DEGREES, 0, 0);     break;
        case SDLK_LEFT:  move_degrees(-HEAD_KEY_NUDGE_DEGREES, 0, 0);    break;
        case SDLK_j:     move_degrees(HEAD_KEY_PAN_DEGREES, 0, 0);      update_face(face, HEAD_KEY_FACE_LOOK, 0); break;
        case SDLK_l:     move_degrees(-HEAD_KEY_PAN_DEGREES, 0, 0);     update_face(face, -HEAD_KEY_FACE_LOOK, 0); break;
        case SDLK_i:     move_degrees(0, HEAD_KEY_TILT_DEGREES, 0);     update_face(face, HEAD_KEY_FACE_LOOK, 0); break;
        case SDLK_k:     move_degrees(0, -HEAD_KEY_TILT_DEGREES, 0);    update_face(face, -HEAD_KEY_FACE_LOOK, 0); break;
        case SDLK_u:     move_degrees(0, 0, HEAD_KEY_HAT_DEGREES);      break;
        case SDLK_o:     move_degrees(0, 0, -HEAD_KEY_HAT_DEGREES);     break;
    }
}
