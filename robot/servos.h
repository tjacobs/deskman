// Drive pan, tilt, and hat

#include <string>
#include <SDL2/SDL.h>
#include "face.h"

// Shorthand for names used below
using namespace std;

int open_servos();
int relax_servos();
void start_servo_position_log();
void stop_servo_position_log();
int set_servo_id(int old_id, int new_id);
void move_servos();
void sweep_servos();
void move_head(int pan_diff, int tilt_diff, int hat_diff);
void move_head(const string &direction);
void look_head(const string &direction, double degrees);
void center();
void get_head_position(int &pan, int &tilt, int &hat);
void set_head_position(int pan, int tilt, int hat);
void get_percent(int &pan, int &tilt, int &hat);
void set_percent(int pan, int tilt, int hat);
void move_percent(int pan_diff, int tilt_diff, int hat_diff);
void handle_servo_keyboard_input(SDL_Event* event, Face* face);
