#include "screen.h"
#include <SDL2/SDL_ttf.h>
#include "face.h"
#include <string>
#include <mutex>
#include <iostream>
#include <cstdlib>
#include <unistd.h>
#include <filesystem>

// Screen globals
SDL_Window* window = nullptr;
SDL_Renderer* renderer = nullptr;
int screen_width = 800;
int screen_height = 600;

static const char* DEFAULT_DISPLAY = ":0";

// A dead IBus socket, GNOME pops its touch keyboard when SDL takes input method focus
static const char* DEAD_IBUS_ADDRESS = "unix:path=/nonexistent";

// Status text
string currentStatus;
mutex statusMutex;

// Set status text
void setStatus(const string& status) {
    unique_lock<mutex> lock(statusMutex);
    if (currentStatus.find("Sleeping") == string::npos || status.find("Sleeping") == string::npos) 
        if (status.find("Listening") == string::npos)
            if (!status.empty())
                cout << status.c_str() << endl;
    currentStatus = status;
}

// Create window
bool create_window() {
    bool fullscreen = false;
    #ifdef __linux__
    fullscreen = true; 
    #endif

    // Default DISPLAY to the local HDMI seat when unset
    if (getenv("DISPLAY") == nullptr || getenv("DISPLAY")[0] == '\0') {
        setenv("DISPLAY", DEFAULT_DISPLAY, 1);
    }

    // Point at the gdm Xauthority file when present
    if (getenv("XAUTHORITY") == nullptr || getenv("XAUTHORITY")[0] == '\0') {
        string xauth = "/run/user/" + to_string(getuid()) + "/gdm/Xauthority";
        if (filesystem::exists(xauth)) setenv("XAUTHORITY", xauth.c_str(), 1);
    }

    // Keep the on-screen keyboard away, SDL connects to IBus while it initializes
    setenv("IBUS_ADDRESS", DEAD_IBUS_ADDRESS, 1);

    // Initialize SDL
    if (SDL_Init(SDL_INIT_VIDEO) < 0) {
        fprintf(stderr, "Error: No available screen\n");
        return false;
    }

    // SDL turns text input on during init, we take no typing
    SDL_StopTextInput();

    // Initialize SDL_image
    if (IMG_Init(IMG_INIT_JPG | IMG_INIT_PNG | IMG_INIT_TIF) == 0) {
        fprintf(stderr, "SDL_image could not initialize: %s\n", SDL_GetError());
        SDL_Quit();
        return false;
    }

    // Initialize SDL_ttf
    if (TTF_Init() < 0) {
        fprintf(stderr, "SDL_ttf could not initialize: %s\n", TTF_GetError());
        SDL_Quit();
        return false;
    }

    // Get screen size
    SDL_DisplayMode display_mode;
    if (SDL_GetCurrentDisplayMode(0, &display_mode) != 0) {
        fprintf(stderr, "No display mode available: %s\n", SDL_GetError());
        IMG_Quit();
        SDL_Quit();
        return false;
    }
    screen_width = display_mode.w;
    screen_height = display_mode.h;

    // Cover the current desktop, exclusive fullscreen can lock the pre-rotate size
    window = SDL_CreateWindow("Deskman Robot", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, screen_width, screen_height, fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
    if (!window) {
        fprintf(stderr, "Window could not be created: %s\n", SDL_GetError());
        IMG_Quit();
        SDL_Quit();
        return false;
    }

    // Use the desktop size the window actually covered
    SDL_GetWindowSize(window, &screen_width, &screen_height);

    // Hide cursor in fullscreen mode
    if (fullscreen) {
        SDL_ShowCursor(SDL_DISABLE);
    }

    // Create renderer for the window
    renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED);
    if (!renderer) {
        fprintf(stderr, "Renderer could not be created: %s\n", SDL_GetError());
        SDL_DestroyWindow(window);
        window = nullptr;
        IMG_Quit();
        SDL_Quit();
        return false;
    }

    // Clear screen
    SDL_SetRenderDrawColor(renderer, 255, 255, 255, 255);
    SDL_RenderClear(renderer);
    SDL_RenderPresent(renderer);
    SDL_Delay(16);
    return true;
}

bool close_window() {
    // Clean up
    if (renderer) {
        SDL_DestroyRenderer(renderer);
        renderer = nullptr;
    }
    if (window) {
        SDL_DestroyWindow(window);
        window = nullptr;
    }
    IMG_Quit();
    SDL_Quit();
    return true;
}

void draw_text(const char* text, int x, int y, TTF_Font* font, SDL_Color color) {
    if (!text || !font) return;
    
    SDL_Surface* textSurface = TTF_RenderText_Solid(font, text, color);
    if (textSurface != NULL) {
        SDL_Texture* textTexture = SDL_CreateTextureFromSurface(renderer, textSurface);
        SDL_Rect textRect = {x, y, textSurface->w, textSurface->h};
        SDL_RenderCopy(renderer, textTexture, NULL, &textRect);
        SDL_FreeSurface(textSurface);
        SDL_DestroyTexture(textTexture);
    }
}

void draw_coordinate_text(Face* face) {
    // Draw coordinate text
    char coordText[100];
    snprintf(coordText, sizeof(coordText), "Head X: %.1f  Y: %.1f", face->currentHeadX * HEAD_SCALE, face->currentHeadY * HEAD_SCALE);
    SDL_Color textColor = {0, 0, 0, 255};
    //draw_text(coordText, 10, 10, face->font, textColor);
}
