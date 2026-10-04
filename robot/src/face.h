#pragma once

// SDL
#include <SDL2/SDL_ttf.h>

// Local
#include "renderer.h"

// Namespace
using namespace std;

// Shapes, font, and animation state the renderer draws the face from
typedef struct {
    // Font for on-screen text
    TTF_Font* font;

    // Vector shapes owned by the renderer
    Ellipse* leftEye;
    Ellipse* rightEye;
    Ellipse* mouth;

    // Blink animation state
    float time;
    float blinkTimer;
    bool isBlinking;

    // Where the head and eyes are pointed
    float lookTiltX;
    float lookTiltY;
    float currentHeadX;
    float currentHeadY;
} Face;

// The one face the whole program animates
extern Face face;

// Degrees the head turns for a full look
extern const float HEAD_LOOK_DEGREES;

// Build the eyes and mouth, then clear the animation state
Face create_face();
void reset_face_animation(Face* face);

// Step the blink and tilt animation, once per frame
void update_face_animation(Face* face);

// Hook for the servo keys, the renderer drives the face itself
void update_face(Face* face, int eye_squint, int smile_curve);

// Show or hide the mouth
void show_mouth(bool show);

// Free the font on the way out
void cleanup_face(Face* face);
