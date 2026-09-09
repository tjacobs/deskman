// Local
#include "face.h"
#include "renderer.h"

// System
#include <cmath>

// Namespace
using namespace std;

// The one face the whole program animates
Face face;

// Blink every ten seconds, the sine closes and opens the lid
static const float ANIMATION_SPEED = 0.02f;
static const float BLINK_SPEED = 8.0f;
static const float BLINK_INTERVAL = 10.0f;

// Eye size, and how far down a blink pulls the lid
static const float EYE_RADIUS_X = 45.0f;
static const float EYE_RADIUS_Y = 120.0f;
static const float EYE_BLINK_CLOSE = 0.9f;

// Eyes sit either side of centre and a little above it
static const float EYE_OFFSET_X = 120.0f;
static const float EYE_OFFSET_Y = -100.0f;

// Mouth is a wide ellipse below centre, with a cutout for the opening
static const float MOUTH_RADIUS_X = 240.0f;
static const float MOUTH_RADIUS_Y = 80.0f;
static const float MOUTH_OFFSET_Y = 200.0f;
static const float MOUTH_CUTOUT_Y = -40.0f;
static const float MOUTH_CUTOUT_HEIGHT = 180.0f;

// Dark grey eyes with a white outline, black mouth
static const SDL_Color EYE_FILL = {25, 25, 25, 255};
static const SDL_Color EYE_STROKE = {255, 255, 255, 255};
static const SDL_Color MOUTH_COLOR = {0, 0, 0, 255};
static const float SHAPE_STROKE_WIDTH = 0.0f;

// Fonts to try for the status text, Mac first then Linux
static const char* MAC_FONT_PATH = "/System/Library/Fonts/Helvetica.ttc";
static const char* LINUX_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf";
static const int FONT_SIZE = 24;

// Degrees the head turns for a full look
const float HEAD_LOOK_DEGREES = 15.0f;

// Build the eyes and mouth, hand them to the renderer, and load the font
Face create_face() {
    Face new_face;

    // Two eyes either side of centre
    new_face.leftEye = new Ellipse(EYE_RADIUS_X, EYE_RADIUS_Y, EYE_FILL, EYE_STROKE, SHAPE_STROKE_WIDTH);
    new_face.leftEye->localPosition = Vec3(-EYE_OFFSET_X, EYE_OFFSET_Y, 0);
    new_face.rightEye = new Ellipse(EYE_RADIUS_X, EYE_RADIUS_Y, EYE_FILL, EYE_STROKE, SHAPE_STROKE_WIDTH);
    new_face.rightEye->localPosition = Vec3(EYE_OFFSET_X, EYE_OFFSET_Y, 0);

    // Mouth carries a cutout for the opening, hidden until talk starts
    new_face.mouth = new Ellipse(MOUTH_RADIUS_X, MOUTH_RADIUS_Y, MOUTH_COLOR, MOUTH_COLOR, SHAPE_STROKE_WIDTH, MOUTH_CUTOUT_Y, MOUTH_CUTOUT_HEIGHT);
    new_face.mouth->localPosition = Vec3(0, MOUTH_OFFSET_Y, 0);
    new_face.mouth->visible = false;

    // Hand the shapes to the renderer
    vectorRenderer.addShape(new_face.leftEye);
    vectorRenderer.addShape(new_face.rightEye);
    vectorRenderer.addShape(new_face.mouth);

    // Load the status font, Mac path first then Linux
    new_face.font = TTF_OpenFont(MAC_FONT_PATH, FONT_SIZE);
    if (!new_face.font) {
        new_face.font = TTF_OpenFont(LINUX_FONT_PATH, FONT_SIZE);
    }
    return new_face;
}

// Clear the blink timer and point the head straight ahead
void reset_face_animation(Face* face) {
    face->time = 0.0f;
    face->blinkTimer = 0.0f;
    face->isBlinking = false;
    face->lookTiltX = 0.0f;
    face->lookTiltY = 0.0f;
    face->currentHeadX = 0.0f;
    face->currentHeadY = 0.0f;
}

// Advance the blink, then tilt the whole face toward where it is looking
void update_face_animation(Face* face) {
    // Start a blink once the interval has passed
    face->time += ANIMATION_SPEED;
    face->blinkTimer += ANIMATION_SPEED;
    if (!face->isBlinking && face->blinkTimer >= BLINK_INTERVAL) {
        face->isBlinking = true;
        face->blinkTimer = 0.0f;
    }

    // Ride a sine down and back up, the blink ends as it crosses zero
    float blinkProgress = 0.0f;
    if (face->isBlinking) {
        blinkProgress = sin(face->blinkTimer * BLINK_SPEED);
        if (blinkProgress < 0) {
            face->isBlinking = false;
            blinkProgress = 0.0f;
        }
    }

    // Squash both eyes by the blink amount
    float eyeHeight = EYE_RADIUS_Y * (1.0f - blinkProgress * EYE_BLINK_CLOSE);
    face->leftEye->radiusY = eyeHeight;
    face->rightEye->radiusY = eyeHeight;

    // Turn the face toward the look target
    vectorRenderer.setFaceRotation(Vec3(-face->lookTiltY, face->lookTiltX, 0));
}

// Hook for the servo keys, the renderer drives the face itself
void update_face(Face* face, int eye_squint, int smile_curve) {
}

// Show or hide the mouth
void show_mouth(bool show) {
    face.mouth->visible = show;
}

// Free the font, the renderer owns the eye and mouth shapes
void cleanup_face(Face* face) {
    if (face->font) {
        TTF_CloseFont(face->font);
        face->font = NULL;
    }
}
