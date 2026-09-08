// Drive pan, tilt, and hat

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
void get_degrees(int &pan, int &tilt, int &hat);
void set_degrees(int pan, int tilt, int hat);
float pan_degrees_from_counts(float counts);
float tilt_degrees_from_counts(float counts);
void move_degrees(float pan_delta, float tilt_delta, float hat_delta);
void handle_servo_keyboard_input(SDL_Event* event, Face* face);
