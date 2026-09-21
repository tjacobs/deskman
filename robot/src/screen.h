#pragma once

// SDL
#include <SDL2/SDL.h>
#include <SDL2/SDL_image.h>
#include <SDL2/SDL_ttf.h>

// System
#include <mutex>
#include <string>

// Namespace
using namespace std;

// Window size, and the renderer everything draws through
extern int screen_width;
extern int screen_height;
extern SDL_Renderer* renderer;

// Latest status text, and the lock guarding it
extern string currentStatus;
extern mutex statusMutex;

// Open and close the fullscreen window
bool create_window();
bool close_window();

// Keep the pointer off the face
void hide_cursor();

// Draw text, and the status bar
void draw_text(const char* text, int x, int y, TTF_Font* font, SDL_Color color);
void draw_status_bar(const char* battery, TTF_Font* font, bool keep_visible);

// Grey bar size, so the menu button can sit inside it
int status_bar_height();
int status_bar_pad();

// Show or hide the grey bar
bool status_bar_visible();
void set_status_bar_visible(bool visible);

// Set the status text
void setStatus(const string& status);

// Tee stdout and stderr into log.txt, and read the newest line back
void start_robot_log();
string last_log_line();
