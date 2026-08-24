#include "face.h"
#include <math.h>
#include <stdio.h>
#include "servos.h"
#include <iostream>
#include "renderer.h"
#include <random>
using namespace std;

// Face
Face face;

// Constants
const float ANIMATION_SPEED = 0.02f;
const float MAX_TILT = 15.0f;
const float BLINK_SPEED = 8.0f;
const float BLINK_INTERVAL = 10.0f;
const float LOOK_INTERVAL = 5.0f;
const float EYE_MOVE_DURATION = 0.5f;
const float HEAD_SCALE = 200.0f;
const float WAIT_DURATION = 1.2f;

Face create_face(int center_x, int center_y) {
    Face face;

    // Create vector shapes for the face
    face.leftEye = new Ellipse(45, 120, {25, 25, 25, 255}, {255, 255, 255, 255}, 0.0f);
    face.leftEye->localPosition = Vec3(-120, -100, 0);
    
    face.rightEye = new Ellipse(45, 120, {25, 25, 25, 255}, {255, 255, 255, 255}, 0.0f);
    face.rightEye->localPosition = Vec3(120, -100, 0);
    
    // Create mouth with cutout (hidden by default)
    face.mouth = new Ellipse(240, 80, {0, 0, 0, 255}, {0, 0, 0, 255}, 0.0f, -40, 180);
    face.mouth->localPosition = Vec3(0, 200, 0);
    face.mouth->visible = false;

    // Add shapes to renderer
    vectorRenderer.addShape(face.leftEye);
    vectorRenderer.addShape(face.rightEye);
    vectorRenderer.addShape(face.mouth);

    // Load font
    face.font = TTF_OpenFont("/System/Library/Fonts/Helvetica.ttc", 24);
    if (!face.font) {
        face.font = TTF_OpenFont("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24);
        if (!face.font) {
            //fprintf(stderr, "Failed to load fallback font: %s\n", TTF_GetError());
        }
    }

    return face;
}

void render_face(SDL_Renderer* renderer, Face* face) {
    // Render eyes
    SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255); // Black for eyes
    SDL_Rect left_eye = {face->eye_left_x - face->eye_width / 2, face->eye_left_y - face->eye_height / 2, face->eye_width, face->eye_height};
    SDL_Rect right_eye = {face->eye_right_x - face->eye_width / 2, face->eye_right_y - face->eye_height / 2, face->eye_width, face->eye_height};
    SDL_RenderFillRect(renderer, &left_eye);
    SDL_RenderFillRect(renderer, &right_eye);

    // Render mouth
    SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255); // Black for mouth line
    int thickness = 5; // Thickness of the mouth line
    
    // Different mouth shapes based on phonemes
    switch (face->mouth_shape) {
        case 'M': // Closed mouth
            for (int t = 0; t < thickness; t++) {
                for (int i = 0; i < face->mouth_width; i++) {
                    int y_offset = (int)(face->mouth_smile * sin(M_PI * i / face->mouth_width));
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y + y_offset + t);
                }
            }
            break;
            
        case 'F': // Slight opening
            for (int t = 0; t < thickness; t++) {
                // Top lip
                for (int i = 0; i < face->mouth_width; i++) {
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y - 5 + t);
                }
                // Bottom lip
                for (int i = 0; i < face->mouth_width; i++) {
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y + 15 + t);
                }
            }
            break;
            
        case 'T': // Wide open
            for (int t = 0; t < thickness; t++) {
                // Top lip
                for (int i = 0; i < face->mouth_width; i++) {
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y - 25 + t);
                }
                // Bottom lip
                for (int i = 0; i < face->mouth_width; i++) {
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y + 25 + t);
                }
            }
            break;
            
        case 'L': // Narrow opening
            for (int t = 0; t < thickness; t++) {
                // Top lip
                for (int i = 0; i < face->mouth_width; i++) {
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y - 3 + t);
                }
                // Bottom lip  
                for (int i = 0; i < face->mouth_width; i++) {
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y + 5 + t);
                }
            }
            break;
            
        default: // Default closed mouth
            for (int t = 0; t < thickness; t++) {
                for (int i = 0; i < face->mouth_width; i++) {
                    int y_offset = (int)(face->mouth_smile * sin(M_PI * i / face->mouth_width));
                    SDL_RenderDrawPoint(renderer, face->mouth_x + i, face->mouth_y + y_offset + t);
                }
            }
    }
}

void update_face(Face* face, int eye_squint, int smile_curve) {
    // Update face parameters based on input
}

void cleanup_face(Face* face) {
    if (face->font) {
        TTF_CloseFont(face->font);
        face->font = NULL;
    }
    // Note: We don't delete the eye shapes here as they're managed by the vectorRenderer
}

void move_face(int smile) {
    // Move the face
    update_face(&face, 0, smile);
}

void reset_face_animation(Face* face) {
    face->time = 0.0f;
    face->blinkTimer = 0.0f;
    face->isBlinking = false;
    face->lookTimer = 0.0f;
    face->isLooking = false;
    face->targetX = 0.0f;
    face->targetY = 0.0f;
    face->lookTiltX = 0.0f;
    face->lookTiltY = 0.0f;
    face->currentHeadX = 0.0f;
    face->currentHeadY = 0.0f;
    face->lookStartTime = 0.0f;
    face->lookDirection = 0;
    face->lookState = 0;
}

void show_mouth(bool show) {
    face.mouth->visible = show;
}

void update_face_animation(Face* face, float deltaTime) {
    // Update time
    face->time += ANIMATION_SPEED;
    
    // Update blinking
    face->blinkTimer += ANIMATION_SPEED;
    if (!face->isBlinking && face->blinkTimer >= BLINK_INTERVAL) {
        face->isBlinking = true;
        face->blinkTimer = 0.0f;
    }

    // Animate eyes
    float blinkProgress = 0.0f;
    if (face->isBlinking) {
        blinkProgress = sin(face->blinkTimer * BLINK_SPEED);
        if (blinkProgress < 0) {
            face->isBlinking = false;
            blinkProgress = 0.0f;
        }
    }

    // Update eye shapes
    float baseHeight = 120.0f;
    float eyeHeight = baseHeight * (1.0f - blinkProgress * 0.9f);
    face->leftEye->radiusY = eyeHeight;
    face->rightEye->radiusY = eyeHeight;

    // Apply combined rotation to the face
    float finalTiltX = sin(face->time) * MAX_TILT + face->lookTiltX;
    float finalTiltY = cos(face->time * 0.5f) * MAX_TILT * 0.3f + face->lookTiltY;
    vectorRenderer.setFaceRotation(Vec3(-face->lookTiltY, face->lookTiltX, 0));
}

void update_face_looking(Face* face, float deltaTime, bool hasFaceTracking) {
    // Update looking behavior
    face->lookTimer += deltaTime;
    
    if (!face->isLooking && face->lookTimer >= LOOK_INTERVAL && !hasFaceTracking) {
        face->isLooking = true;
        face->lookTimer = 0.0f;
        face->lookStartTime = face->time;
        
        // Choose random look target (-1 to 1 range)
        face->targetX = (rand() % 200 - 100) / 100.0f;
        face->targetY = (rand() % 200 - 100) / 100.0f;
        
        // Reset current positions
        face->lookTiltX = 0.0f;
        face->lookTiltY = 0.0f;
        face->currentHeadX = 0.0f;
        face->currentHeadY = 0.0f;
    }

    // Look
    if (face->isLooking && !hasFaceTracking) {
        float lookTime = face->time - face->lookStartTime;
        
        switch (face->lookState) {
            case 0:  // Initial look with eye movement
                if (lookTime < EYE_MOVE_DURATION) {
                    float t = lookTime / EYE_MOVE_DURATION;
                    face->lookTiltX = face->targetX * t * 20.0f;
                    face->lookTiltY = face->targetY * t * 20.0f;
                } else {
                    face->lookState = 2;
                    face->lookStartTime = face->time;
                }
                break;

            case 2:  // Move head
                move_head(face->targetX * HEAD_SCALE, face->targetY * HEAD_SCALE, 0);
                face->currentHeadX = face->targetX;
                face->currentHeadY = face->targetY;
                face->lookState = 3;
                face->lookStartTime = face->time;
                break;

            case 3:  // Wait at new position and transition eyes back
                if (lookTime < EYE_MOVE_DURATION) {
                    float t = lookTime / EYE_MOVE_DURATION;
                    face->lookTiltX = face->targetX * (1.0f - t) * 20.0f;
                    face->lookTiltY = face->targetY * (1.0f - t) * 20.0f;
                } else if (lookTime >= WAIT_DURATION) {
                    face->lookState = 4;
                    face->lookStartTime = face->time;
                    face->lookTiltX = 0.0f;
                    face->lookTiltY = 0.0f;
                }
                break;

            case 4:  // Move back
                move_head(-face->targetX * HEAD_SCALE, -face->targetY * HEAD_SCALE, 0);
                face->currentHeadX = 0.0f;
                face->currentHeadY = 0.0f;
                face->lookState = 5;
                face->lookStartTime = face->time;
                break;

            case 5:  // Final wait before ending cycle
                if (lookTime >= WAIT_DURATION) {
                    face->lookTiltX = 0.0f;
                    face->lookTiltY = 0.0f;
                    face->isLooking = false;
                    face->lookState = 0;
                }
                break;
        }
    }
}

