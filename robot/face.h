#ifndef FACE_H
#define FACE_H

#include <SDL2/SDL.h>
#include <SDL2/SDL_image.h>
#include <SDL2/SDL_ttf.h>
#include "renderer.h"
#include <thread>
#include <chrono>

using namespace std;

typedef struct {
    // Eye parameters
    int eye_left_x;   // X position of the left eye
    int eye_left_y;   // Y position of the left eye
    int eye_right_x;  // X position of the right eye
    int eye_right_y;  // Y position of the right eye
    int eye_width;    // Width of the eyes
    int eye_height;   // Height of the eyes (changes to simulate squinting)

    // Mouth parameters
    int mouth_x;      // X position of the mouth
    int mouth_y;      // Y position of the mouth
    int mouth_width;  // Width of the mouth
    int mouth_height; // Height of the mouth
    int mouth_smile;  // Curve for the smile (positive for smile, negative for frown)
    char mouth_shape; // Shape of mouth for different phonemes (M=closed, F=slight, T=wide, L=narrow)

    // Font for text rendering
    TTF_Font* font;

    Ellipse* leftEye;
    Ellipse* rightEye;
    Ellipse* mouth;

    // Animation state
    float time;
    float blinkTimer;
    bool isBlinking;
    float lookTimer;
    bool isLooking;
    float targetX;
    float targetY;
    float lookTiltX;
    float lookTiltY;
    float currentHeadX;
    float currentHeadY;
    float lookStartTime;
    int lookDirection;
    int lookState;

} Face;

extern Face face;

// Constants
extern const float ANIMATION_SPEED;
extern const float MAX_TILT;
extern const float BLINK_SPEED;
extern const float BLINK_INTERVAL;
extern const float LOOK_INTERVAL;
extern const float EYE_MOVE_DURATION;
extern const float HEAD_SCALE;
extern const float WAIT_DURATION;

Face create_face(int center_x, int center_y);
void update_face(Face* face, int eye_squint, int smile_curve);
void cleanup_face(Face* face);
void update_face_animation(Face* face, float deltaTime);
void update_face_looking(Face* face, float deltaTime, bool hasFaceTracking);
void reset_face_animation(Face* face);
void show_mouth(bool show);

#endif
