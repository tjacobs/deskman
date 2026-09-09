#pragma once

// SDL
#include <SDL2/SDL.h>

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

// Route a screen tap to the Call and Exit buttons
void handle_call_event(const SDL_Event& event);

// True while the peer list is up
bool call_overlay_open();

// True while face tracking should follow, off after ready following a listen
bool listen_open();
