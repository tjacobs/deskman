#pragma once

// SDL
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>

// What a caller wants the camera to do around a video call
const int CALL_HANDOFF_NONE = 0;
const int CALL_HANDOFF_PAUSE = 1;
const int CALL_HANDOFF_RESUME = 2;

// Open and close the Unix socket other programs talk to
bool start_interface();
void stop_interface();

// Pick up a pause or resume request, then report how it went
int take_call_handoff();
void complete_call_handoff(bool ok);

// Route a screen tap to the menu button and the popup items
void handle_call_event(const SDL_Event& event);

// Draw the menu button, and the popup above it when it is open
void draw_menu(TTF_Font* font);

// True while the popup list is showing, and where the button starts
bool menu_open();
int menu_button_left();

// True while the peer list is up
bool call_overlay_open();

// Camera, Move, Audio, and Record presses since the last check, then clear them
void take_menu_presses(bool& camera, bool& move, bool& audio, bool& record);

// True while face tracking should follow, off in ready until talk hears the wake word
bool listen_open();

// Which model talk runs, and no request waiting on the Mode button
const int TALK_MODE_LOCAL = 0;
const int TALK_MODE_CLOUD = 1;
const int TALK_MODE_REALTIME = 2;
const int TALK_MODE_NONE = -1;

// Show the mode talk is running, take the mode the Mode button settled on, and name one
void set_talk_mode(int mode);
int take_talk_mode_request();
const char* talk_mode_name(int mode);
