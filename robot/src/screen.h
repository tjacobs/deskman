#ifndef SCREEN_H
#define SCREEN_H

#include <SDL2/SDL.h>
#include <SDL2/SDL_image.h>
#include <SDL2/SDL_ttf.h>
#include <string>
#include <mutex>

using namespace std;

// Screen
extern int screen_width;
extern int screen_height;
extern SDL_Renderer* renderer;

// Status
extern string currentStatus;
extern mutex statusMutex;

// Screen functions
bool create_window();
bool close_window();
void draw_text(const char* text, int x, int y, TTF_Font* font, SDL_Color color);
void draw_bottom_bar(const char* battery, TTF_Font* font, bool show_exit);
bool tap_is_exit(int x, int y);
bool tap_is_call(int x, int y);
bool status_bar_visible();
void set_status_bar_visible(bool visible);
void setStatus(const string& status);
void start_robot_log();
string last_log_line();

#endif

