// Drive servos

#pragma once

// SDL
#include <SDL2/SDL.h>

// Local
#include "face.h"

// Shorthand for names used below
using namespace std;

// Open the servo bus, and turn torque off again
int open_servos();
int relax_servos();

// Print every servo position on a timer
void start_servo_position_log();
void stop_servo_position_log();

// Write a new bus ID into a servo's EEPROM
int set_servo_id(int old_id, int new_id);

// Send the commanded positions
void move_servos();

// Sweep the whole travel range
void sweep_servos();

// Read and write the head pose in degrees
void get_degrees(int &pan, int &tilt, int &hat);
void set_degrees(int pan, int tilt, int hat);
void move_degrees(float pan_delta, float tilt_delta, float hat_delta);

// Turn a count nudge into degrees
float pan_degrees_from_counts(float counts);
float tilt_degrees_from_counts(float counts);

// Nudge the head with the arrow keys
void handle_servo_keyboard_input(SDL_Event* event, Face* face);
