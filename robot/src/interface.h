// Unix socket for other programs to move the head, pause the camera, and open the menu.

#pragma once

#include <SDL2/SDL.h>

const int CALL_HANDOFF_NONE = 0;
const int CALL_HANDOFF_PAUSE = 1;
const int CALL_HANDOFF_RESUME = 2;

bool start_interface();
void stop_interface();
int take_call_handoff();
void complete_call_handoff(bool ok);
void handle_call_event(const SDL_Event& event);
bool call_overlay_open();
bool listen_open();
