/*
 * Drag on the call video to steer the far robot's head.
*/

// Includes
#include "look.h"
#include "video.h"

#include <chrono>
#include <iostream>
#include <string>

#ifdef HAVE_X11_FULLSCREEN
#include <X11/Xlib.h>
#include <X11/extensions/shape.h>
#endif

using namespace std;
using namespace std::chrono;

// Joystick circles, the radius and green the web client draws
const int LOOK_CIRCLE_RADIUS = 40;
const char* LOOK_ORIGIN_COLOR = "#808080";
const char* LOOK_FINGER_COLOR = "#22A570";

// Ignore the first few pixels of a drag, then step at the rate the web client repeats
const int LOOK_DEAD_ZONE = 5;
const int LOOK_REPEAT_MS = 100;

// Leave the hangup strip and camera chip to the call overlay
const int LOOK_BAR_HEIGHT = 96;
const int LOOK_CAMERA_WIDTH = 110;
const int LOOK_CAMERA_HEIGHT = 74;
const int LOOK_CAMERA_PAD = 16;

// Where to send the steer
static WebSocket* lookWebSocket = NULL;
static string lookDeviceName;

#ifdef HAVE_X11_FULLSCREEN

// Circles sit on the root, video and the call bar each have their own connection
static Display* lookDisplay = NULL;
static Window lookOriginWindow = 0;
static Window lookFingerWindow = 0;

// The finger, in screen pixels
static bool lookDown = false;
static bool lookIgnorePress = false;
static int lookStartX = 0;
static int lookStartY = 0;
static int lookX = 0;
static int lookY = 0;
static int lastLookPan = 0;
static int lastLookTilt = 0;
static steady_clock::time_point lastLookSend;

static void pollLookPointer();
static bool isLookChrome(int x, int y);
static bool ensureLookCircles();
static Window createCircleWindow(const char* colorName);
static void showCircles();
static void placeCircle(Window window, int x, int y);
static void hideCircles();
static void sendLookStep();
static void sendLookValues(int pan, int tilt);
static void resetLook();
static int ignoreLookXErrors(Display* display, XErrorEvent* error);

// Open a connection of our own, the video and overlay threads each have theirs
void initLook(WebSocket* webSocketPointer, string deviceNameValue) {
    lookWebSocket = webSocketPointer;
    lookDeviceName = deviceNameValue;
    XSetErrorHandler(ignoreLookXErrors);
    lookDisplay = XOpenDisplay(NULL);
    if (!lookDisplay) cout << "Drag to look disabled, no X display." << endl;
}

// Read the pointer on the root, the call bar grab eats window events
void pollLook() {
    if (!isVideoRunning()) {
        resetLook();
        return;
    }
    pollLookPointer();
    sendLookStep();
}

// Follow button 1 on the screen, skip presses that start on hangup or the camera chip
static void pollLookPointer() {
    Window rootReturn;
    Window childReturn;
    int rootX = 0;
    int rootY = 0;
    int windowX = 0;
    int windowY = 0;
    unsigned int buttonMask = 0;
    if (!XQueryPointer(lookDisplay, RootWindow(lookDisplay, DefaultScreen(lookDisplay)), &rootReturn, &childReturn, &rootX, &rootY, &windowX, &windowY, &buttonMask)) return;

    // The call overlay already owns taps on its own chrome
    bool buttonDown = (buttonMask & Button1Mask) != 0;
    if (buttonDown && !lookDown && !lookIgnorePress) {
        if (isLookChrome(rootX, rootY)) {
            lookIgnorePress = true;
            return;
        }
        if (!ensureLookCircles()) return;
        lookDown = true;
        lookStartX = rootX;
        lookStartY = rootY;
        lookX = rootX;
        lookY = rootY;
        showCircles();
        return;
    }

    // Carry the green circle along
    if (buttonDown && lookDown) {
        lookX = rootX;
        lookY = rootY;
        placeCircle(lookFingerWindow, lookX, lookY);
        return;
    }

    // Let go and the far head stops
    if (!buttonDown && lookDown) resetLook();
    if (!buttonDown) lookIgnorePress = false;
}

// True on the hangup strip or the top camera chip
static bool isLookChrome(int x, int y) {
    int screen = DefaultScreen(lookDisplay);
    int width = DisplayWidth(lookDisplay, screen);
    int height = DisplayHeight(lookDisplay, screen);
    if (y >= height - LOOK_BAR_HEIGHT) return true;
    int cameraX = (width - LOOK_CAMERA_WIDTH) / 2;
    return x >= cameraX && x < cameraX + LOOK_CAMERA_WIDTH && y >= LOOK_CAMERA_PAD && y < LOOK_CAMERA_PAD + LOOK_CAMERA_HEIGHT;
}

// Build the two circles once, they stay hidden until a finger lands
static bool ensureLookCircles() {
    if (!lookDisplay) return false;
    if (lookOriginWindow && lookFingerWindow) return true;
    lookOriginWindow = createCircleWindow(LOOK_ORIGIN_COLOR);
    lookFingerWindow = createCircleWindow(LOOK_FINGER_COLOR);
    return lookOriginWindow && lookFingerWindow;
}

// One circle of solid colour, input falls through so QueryPointer still sees the finger
static Window createCircleWindow(const char* colorName) {
    int screen = DefaultScreen(lookDisplay);
    int size = LOOK_CIRCLE_RADIUS * 2;
    XColor color;
    Colormap colormap = DefaultColormap(lookDisplay, screen);
    if (!XParseColor(lookDisplay, colormap, colorName, &color)) return 0;
    if (!XAllocColor(lookDisplay, colormap, &color)) return 0;

    // Keep it out of the window manager's hands
    XSetWindowAttributes attributes;
    attributes.override_redirect = True;
    Window window = XCreateWindow(lookDisplay, RootWindow(lookDisplay, screen), 0, 0, size, size, 0, CopyFromParent, InputOutput, CopyFromParent, CWOverrideRedirect, &attributes);
    if (!window) return 0;
    XSetWindowBackground(lookDisplay, window, color.pixel);

    // Cut the square down to a circle
    Pixmap mask = XCreatePixmap(lookDisplay, window, size, size, 1);
    GC maskContext = XCreateGC(lookDisplay, mask, 0, NULL);
    XSetForeground(lookDisplay, maskContext, 0);
    XFillRectangle(lookDisplay, mask, maskContext, 0, 0, size, size);
    XSetForeground(lookDisplay, maskContext, 1);
    XFillArc(lookDisplay, mask, maskContext, 0, 0, size, size, 0, 360 * 64);
    XShapeCombineMask(lookDisplay, window, ShapeBounding, 0, 0, mask, ShapeSet);
    XFreeGC(lookDisplay, maskContext);
    XFreePixmap(lookDisplay, mask);
    XShapeCombineRectangles(lookDisplay, window, ShapeInput, 0, 0, NULL, 0, ShapeSet, Unsorted);
    return window;
}

// Put the grey circle where the finger landed and the green one on the finger
static void showCircles() {
    placeCircle(lookOriginWindow, lookStartX, lookStartY);
    placeCircle(lookFingerWindow, lookX, lookY);
    if (lookOriginWindow) XMapRaised(lookDisplay, lookOriginWindow);
    if (lookFingerWindow) XMapRaised(lookDisplay, lookFingerWindow);
    XFlush(lookDisplay);
}

// Centre one circle on a point, and keep it above the video
static void placeCircle(Window window, int x, int y) {
    if (!window) return;
    XMoveWindow(lookDisplay, window, x - LOOK_CIRCLE_RADIUS, y - LOOK_CIRCLE_RADIUS);
    XRaiseWindow(lookDisplay, window);
    XFlush(lookDisplay);
}

// Take both circles away
static void hideCircles() {
    if (lookOriginWindow) XUnmapWindow(lookDisplay, lookOriginWindow);
    if (lookFingerWindow) XUnmapWindow(lookDisplay, lookFingerWindow);
    XFlush(lookDisplay);
}

// Step the far head while the finger sits outside the dead zone
static void sendLookStep() {
    if (!lookDown) return;
    auto now = steady_clock::now();
    if (lastLookSend.time_since_epoch().count() != 0 && duration_cast<milliseconds>(now - lastLookSend).count() < LOOK_REPEAT_MS) return;
    lastLookSend = now;

    // Drag right to look right, drag up to look up, screen y grows downwards
    int offsetX = lookX - lookStartX;
    int offsetY = lookStartY - lookY;
    int pan = 0;
    int tilt = 0;
    if (offsetX > LOOK_DEAD_ZONE) pan = 1;
    if (offsetX < -LOOK_DEAD_ZONE) pan = -1;
    if (offsetY > LOOK_DEAD_ZONE) tilt = 1;
    if (offsetY < -LOOK_DEAD_ZONE) tilt = -1;
    sendLookValues(pan, tilt);
}

// The same x and y messages the web client sends, y pans and x tilts
static void sendLookValues(int pan, int tilt) {
    if (!lookWebSocket) return;
    if (pan != 0 || lastLookPan != 0) lookWebSocket->send(lookDeviceName + " y " + to_string(pan));
    if (tilt != 0 || lastLookTilt != 0) lookWebSocket->send(lookDeviceName + " x " + to_string(tilt));
    lastLookPan = pan;
    lastLookTilt = tilt;
}

// Hide the circles and stop the far head
static void resetLook() {
    if (!lookDown) return;
    lookDown = false;
    hideCircles();
    sendLookValues(0, 0);
}

// Close the connection at exit
void stopLook() {
    resetLook();
    if (!lookDisplay) return;
    if (lookFingerWindow) XDestroyWindow(lookDisplay, lookFingerWindow);
    if (lookOriginWindow) XDestroyWindow(lookDisplay, lookOriginWindow);
    lookFingerWindow = 0;
    lookOriginWindow = 0;
    XCloseDisplay(lookDisplay);
    lookDisplay = NULL;
}

// A destroyed window is not worth quitting over
static int ignoreLookXErrors(Display* display, XErrorEvent* error) {
    (void)display;
    (void)error;
    return 0;
}

#else

// Without X11 there is no video window to drag on
void initLook(WebSocket* webSocketPointer, string deviceNameValue) {
    lookWebSocket = webSocketPointer;
    lookDeviceName = deviceNameValue;
}

void pollLook() {
}

void stopLook() {
}

#endif
