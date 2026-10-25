// Local
#include "face.h"
#include "renderer.h"

// System
#include <cmath>

// Namespace
using namespace std;

// The one face the whole program animates
Face face;

// Animation clock units per real second, how often to blink, and how fast the sine closes and opens the lid
static const float ANIMATION_SPEED = 0.4f;
static const float BLINK_SPEED = 8.0f;
static const float BLINK_INTERVAL = 10.0f;

// How long the face takes to glide most of the way to a new look target
static const float LOOK_EASE_MS = 100.0f;

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

// Dark grey eyes and a black mouth
static const SDL_Color EYE_FILL = {25, 25, 25, 255};
static const SDL_Color MOUTH_COLOR = {0, 0, 0, 255};

// Bow tie sits below the mouth, a dark tie with a lighter knot
static const float BOW_TIE_OFFSET_Y = 350.0f;
static const float BOW_TIE_WING_HALF_WIDTH = 90.0f;
static const float BOW_TIE_WING_HALF_HEIGHT = 40.0f;
static const float BOW_TIE_KNOT_HALF_WIDTH = 16.0f;
static const float BOW_TIE_KNOT_HALF_HEIGHT = 26.0f;
static const SDL_Color BOW_TIE_COLOR = {25, 25, 25, 255};
static const SDL_Color BOW_TIE_KNOT_COLOR = {90, 90, 90, 255};

// Party background is a rainbow mesh whose hue drifts over time, softened so the dark face stands out
static const int PARTY_COLUMNS = 16;
static const int PARTY_ROWS = 10;
static const float PARTY_SATURATION = 0.45f;
static const float PARTY_VALUE = 1.0f;
static const float PARTY_HUE_SPEED = 0.12f;
static const float PARTY_HUE_SPREAD_X = 0.6f;
static const float PARTY_HUE_SPREAD_Y = 0.4f;

// Confetti pieces fall over the rainbow, each with its own column, speed, and sway
static const int CONFETTI_COUNT = 40;
static const int CONFETTI_SIZE = 14;
static const float CONFETTI_SATURATION = 0.9f;
static const float CONFETTI_FALL_SPEED = 0.15f;
static const float CONFETTI_SWAY_PIXELS = 20.0f;
static const float CONFETTI_COLOR_SPEED = 0.2f;

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
    new_face.leftEye = new Ellipse(EYE_RADIUS_X, EYE_RADIUS_Y, EYE_FILL);
    new_face.leftEye->localPosition = Vec3(-EYE_OFFSET_X, EYE_OFFSET_Y, 0);
    new_face.rightEye = new Ellipse(EYE_RADIUS_X, EYE_RADIUS_Y, EYE_FILL);
    new_face.rightEye->localPosition = Vec3(EYE_OFFSET_X, EYE_OFFSET_Y, 0);

    // Mouth carries a cutout for the opening, hidden until talk starts
    new_face.mouth = new Ellipse(MOUTH_RADIUS_X, MOUTH_RADIUS_Y, MOUTH_COLOR);
    new_face.mouth->cutoutY = MOUTH_CUTOUT_Y;
    new_face.mouth->cutoutHeight = MOUTH_CUTOUT_HEIGHT;
    new_face.mouth->localPosition = Vec3(0, MOUTH_OFFSET_Y, 0);
    new_face.mouth->visible = false;

    // Bow tie below the mouth, hidden until the party starts
    new_face.bowTie = new BowTie(BOW_TIE_WING_HALF_WIDTH, BOW_TIE_WING_HALF_HEIGHT, BOW_TIE_KNOT_HALF_WIDTH, BOW_TIE_KNOT_HALF_HEIGHT, BOW_TIE_COLOR, BOW_TIE_KNOT_COLOR);
    new_face.bowTie->localPosition = Vec3(0, BOW_TIE_OFFSET_Y, 0);
    new_face.bowTie->visible = false;

    // Hand the shapes to the renderer
    vectorRenderer.addShape(new_face.leftEye);
    vectorRenderer.addShape(new_face.rightEye);
    vectorRenderer.addShape(new_face.mouth);
    vectorRenderer.addShape(new_face.bowTie);

    // Load the status font, Mac path first then Linux
    new_face.font = TTF_OpenFont(MAC_FONT_PATH, scale_to_screen(FONT_SIZE));
    if (!new_face.font) {
        new_face.font = TTF_OpenFont(LINUX_FONT_PATH, scale_to_screen(FONT_SIZE));
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
    face->lookTargetX = 0.0f;
    face->lookTargetY = 0.0f;
    face->currentHeadX = 0.0f;
    face->currentHeadY = 0.0f;
}

// Advance the blink, then tilt the whole face toward where it is looking
void update_face_animation(Face* face) {
    // Measure the real time since the last frame, so the speed holds at any frame rate
    static Uint32 last_ticks = SDL_GetTicks();
    Uint32 now_ticks = SDL_GetTicks();
    float elapsed_milliseconds = (float)(now_ticks - last_ticks);
    float step = elapsed_milliseconds / 1000.0f * ANIMATION_SPEED;
    last_ticks = now_ticks;

    // Start a blink once the interval has passed
    face->time += step;
    face->blinkTimer += step;
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

    // Glide the tilt a fraction of the way to its target, so slow face tracking still looks smooth
    float glide = 1.0f - exp(-elapsed_milliseconds / LOOK_EASE_MS);
    face->lookTiltX += (face->lookTargetX - face->lookTiltX) * glide;
    face->lookTiltY += (face->lookTargetY - face->lookTiltY) * glide;

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

// Show or hide the bow tie
void show_party(bool show) {
    face.bowTie->visible = show;
}

// Turn a hue, saturation, and value into a colour, the hue wraps around
static SDL_Color hue_color(float hue, float saturation, float value) {
    // Pick the sixth of the colour wheel and how far through it
    float position = (hue - floorf(hue)) * 6.0f;
    int sector = (int)position;
    float fraction = position - sector;

    // Work out the three levels the sector mixes
    float low = value * (1.0f - saturation);
    float falling = value * (1.0f - saturation * fraction);
    float rising = value * (1.0f - saturation * (1.0f - fraction));
    float red = value, green = rising, blue = low;
    if (sector == 1) { red = falling; green = value; blue = low; }
    if (sector == 2) { red = low; green = value; blue = rising; }
    if (sector == 3) { red = low; green = falling; blue = value; }
    if (sector == 4) { red = rising; green = low; blue = value; }
    if (sector == 5) { red = value; green = low; blue = falling; }
    return {(Uint8)(red * 255), (Uint8)(green * 255), (Uint8)(blue * 255), 255};
}

// Paint a drifting rainbow over the whole screen, then confetti falling over it
void draw_party_background() {
    // Time in seconds drives the hue drift and the confetti
    float seconds = SDL_GetTicks() / 1000.0f;

    // Colour a grid of points by hue, so the graphics card blends between them
    SDL_Vertex vertices[(PARTY_COLUMNS + 1) * (PARTY_ROWS + 1)];
    for (int row = 0; row <= PARTY_ROWS; row++) {
        for (int column = 0; column <= PARTY_COLUMNS; column++) {
            float across = (float)column / PARTY_COLUMNS;
            float down = (float)row / PARTY_ROWS;
            float hue = across * PARTY_HUE_SPREAD_X + down * PARTY_HUE_SPREAD_Y + seconds * PARTY_HUE_SPEED;
            vertices[row * (PARTY_COLUMNS + 1) + column] = {{across * screen_width, down * screen_height}, hue_color(hue, PARTY_SATURATION, PARTY_VALUE), {0, 0}};
        }
    }

    // Join the grid into two triangles per cell
    int indices[PARTY_COLUMNS * PARTY_ROWS * 6];
    int count = 0;
    for (int row = 0; row < PARTY_ROWS; row++) {
        for (int column = 0; column < PARTY_COLUMNS; column++) {
            int topLeft = row * (PARTY_COLUMNS + 1) + column;
            int bottomLeft = topLeft + PARTY_COLUMNS + 1;
            int cell[6] = {topLeft, topLeft + 1, bottomLeft, topLeft + 1, bottomLeft + 1, bottomLeft};
            for (int index : cell)
                indices[count++] = index;
        }
    }
    SDL_RenderGeometry(renderer, nullptr, vertices, (PARTY_COLUMNS + 1) * (PARTY_ROWS + 1), indices, count);

    // Drop each confetti piece from the top, its column and speed come from its number
    int size = scale_to_screen(CONFETTI_SIZE);
    for (int piece = 0; piece < CONFETTI_COUNT; piece++) {
        float column = fabsf(sinf(piece * 12.9898f) * 43758.5453f);
        column -= floorf(column);
        float speed = 0.5f + fabsf(sinf(piece * 78.233f));
        float fall = piece / (float)CONFETTI_COUNT + seconds * CONFETTI_FALL_SPEED * speed;
        fall -= floorf(fall);
        SDL_Rect rect = {(int)(column * screen_width + sinf(seconds * 2.0f + piece) * scale_to_screen(CONFETTI_SWAY_PIXELS)), (int)(fall * screen_height), size, size};
        SDL_Color color = hue_color(piece / (float)CONFETTI_COUNT + seconds * CONFETTI_COLOR_SPEED, CONFETTI_SATURATION, PARTY_VALUE);
        SDL_SetRenderDrawColor(renderer, color.r, color.g, color.b, 255);
        SDL_RenderFillRect(renderer, &rect);
    }
}

// Free the font, the renderer owns the eye and mouth shapes
void cleanup_face(Face* face) {
    if (face->font) {
        TTF_CloseFont(face->font);
        face->font = NULL;
    }
}
