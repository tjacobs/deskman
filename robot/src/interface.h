#pragma once

// SDL
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>

// System
#include <string>

// Namespace
using namespace std;

// What a caller wants the camera to do around a video call
const int CALL_HANDOFF_NONE = 0;
const int CALL_HANDOFF_PAUSE = 1;
const int CALL_HANDOFF_RESUME = 2;

// Open and close the Unix socket other programs talk to, and say where recordings are kept
bool start_interface(const string& recordings_path);
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

// Draw the recordings list or the playing video, and say when it is up
void draw_video_list(TTF_Font* font);
bool video_list_open();

// Draw the wireless networks, and say when that list is up
void draw_wifi_list(TTF_Font* font);
bool wifi_list_open();

// Camera, Move, and Audio presses since the last check, then clear them
void take_menu_presses(bool& camera, bool& move, bool& audio);

// What the Record button or a voice command last asked for
const int RECORD_REQUEST_NONE = 0;
const int RECORD_REQUEST_TOGGLE = 1;
const int RECORD_REQUEST_START = 2;
const int RECORD_REQUEST_STOP = 3;

// Take the recording request, and a request to play the newest recording
int take_record_request();
bool take_play_request();

// True while face tracking should follow, off in ready until talk hears the wake word
bool listen_open();

// Which model talk runs, and no request waiting on the Mode button
const int TALK_MODE_LOCAL = 0;
const int TALK_MODE_CLOUD = 1;
const int TALK_MODE_REALTIME = 2;
const int TALK_MODE_NONE = -1;

// Show whether the camera preview is up, so the Camera button can say so
void set_camera_showing(bool showing);

// Show the mode talk is running, take the mode the Mode button settled on, and name one
void set_talk_mode(int mode);
int take_talk_mode_request();
const char* talk_mode_name(int mode);
