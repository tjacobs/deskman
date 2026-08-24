/*
 * Always-on-top call menu.
*/

#include "call.h"

#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>

#include <atomic>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>

#ifdef __linux__
#include <X11/Xlib.h>
#endif

using namespace std;

const int CALL_BUTTON_HEIGHT = 88;
const int CALL_ROW_HEIGHT = 80;
const int HANGUP_STRIP_HEIGHT = 96;
const int CALL_FONT_SIZE = 32;
const char* CALL_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf";

enum CallInterfaceView {
    CALL_INTERFACE_IDLE,
    CALL_INTERFACE_DIRECTORY,
    CALL_INTERFACE_INCOMING,
    CALL_INTERFACE_IN_CALL
};

static mutex callInterfaceMutex;
static atomic<bool> callInterfaceReady{false};
static CallInterfaceView requestedView = CALL_INTERFACE_IDLE;
static CallInterfaceView shownView = CALL_INTERFACE_IDLE;
static vector<string> callPeers;
static string incomingPeer;
static string callStatus;
static string pendingAction;
static string pendingPeer;

static SDL_Window* callWindow = nullptr;
static SDL_Renderer* callRenderer = nullptr;
static TTF_Font* callFont = nullptr;
static int windowWidth = 0;
static int windowHeight = 0;
static bool overlayMapped = false;
#ifdef __linux__
static Display* overlayDisplay = nullptr;
static Window overlayXWindow = 0;
#endif
static SDL_Rect backButton;
static SDL_Rect hangupButton;
static SDL_Rect acceptButton;
static SDL_Rect declineButton;
static vector<SDL_Rect> peerButtons;

static void applyRequestedView();
static void readDisplaySize();
static bool ensureOverlayWindow();
static void mapOverlay(int y, int height);
static void unmapOverlay();
static void destroyOverlayWindow();
static void layoutButtons();
static void drawOverlay();
static void drawButton(const SDL_Rect& rect, const string& label, SDL_Color fill, SDL_Color text);
static void drawLabel(const char* text, int x, int y, SDL_Color color);
static void handleOverlayEvent(const SDL_Event& event);
static void handleTap(int x, int y);
static void pollXTaps();
static bool hitRect(const SDL_Rect& rect, int x, int y);
static void queueAction(string command, string peer);
static void readTap(const SDL_Event& event, int& x, int& y, bool& tap);

void startCallInterface() {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS, "1");
    SDL_SetHint(SDL_HINT_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR, "1");
    SDL_SetHint(SDL_HINT_VIDEO_MINIMIZE_ON_FOCUS_LOSS, "0");
    if (SDL_Init(SDL_INIT_VIDEO) < 0) {
        cout << "Call interface skipped, SDL init failed: " << SDL_GetError() << endl;
        return;
    }
    if (TTF_Init() < 0) {
        cout << "Call interface skipped, TTF init failed: " << TTF_GetError() << endl;
        SDL_Quit();
        return;
    }
    callFont = TTF_OpenFont(CALL_FONT_PATH, CALL_FONT_SIZE);
    if (!callFont) {
        cout << "Call interface skipped, font missing: " << CALL_FONT_PATH << endl;
        TTF_Quit();
        SDL_Quit();
        return;
    }
    callInterfaceReady = true;
}

void stopCallInterface() {
    callInterfaceReady = false;
    destroyOverlayWindow();
    if (callFont) {
        TTF_CloseFont(callFont);
        callFont = nullptr;
    }
    TTF_Quit();
    SDL_Quit();
}

void pollCallInterface() {
    if (!callInterfaceReady.load()) return;
    applyRequestedView();
    if (!callWindow || !overlayMapped) return;
    pollXTaps();
#ifdef __linux__
    if (overlayDisplay && overlayXWindow) {
        XRaiseWindow(overlayDisplay, overlayXWindow);
        XFlush(overlayDisplay);
    }
#else
    SDL_RaiseWindow(callWindow);
#endif
    SDL_Event event;
    while (SDL_PollEvent(&event)) {
        if (event.type == SDL_QUIT) queueAction("hangup", "");
        else handleOverlayEvent(event);
    }
    applyRequestedView();
    if (callWindow && overlayMapped) drawOverlay();
}

bool toggleCallDirectory() {
    lock_guard<mutex> lock(callInterfaceMutex);
    if (requestedView == CALL_INTERFACE_DIRECTORY) {
        requestedView = CALL_INTERFACE_IDLE;
        callStatus.clear();
        return false;
    }
    if (requestedView != CALL_INTERFACE_IDLE) return false;
    requestedView = CALL_INTERFACE_DIRECTORY;
    callPeers.clear();
    callStatus.clear();
    return true;
}

void showIncomingCall(string peer) {
    lock_guard<mutex> lock(callInterfaceMutex);
    incomingPeer = peer;
    callStatus.clear();
    requestedView = CALL_INTERFACE_INCOMING;
}

void showCallInProgress() {
    lock_guard<mutex> lock(callInterfaceMutex);
    callStatus.clear();
    requestedView = CALL_INTERFACE_IN_CALL;
}

void showCallIdle() {
    lock_guard<mutex> lock(callInterfaceMutex);
    incomingPeer.clear();
    requestedView = CALL_INTERFACE_IDLE;
}

bool isCallOverlayIdle() {
    lock_guard<mutex> lock(callInterfaceMutex);
    return requestedView == CALL_INTERFACE_IDLE;
}

void showCallFailed(string message) {
    lock_guard<mutex> lock(callInterfaceMutex);
    callStatus = message;
    requestedView = CALL_INTERFACE_DIRECTORY;
}

void setCallPeers(const vector<string>& names) {
    lock_guard<mutex> lock(callInterfaceMutex);
    callPeers = names;
}

bool takeCallAction(string& command, string& peer) {
    lock_guard<mutex> lock(callInterfaceMutex);
    if (pendingAction.empty()) return false;
    command = pendingAction;
    peer = pendingPeer;
    pendingAction.clear();
    pendingPeer.clear();
    return true;
}

bool isCallInterfaceReady() {
    return callInterfaceReady.load();
}

static void applyRequestedView() {
    CallInterfaceView view;
    {
        lock_guard<mutex> lock(callInterfaceMutex);
        view = requestedView;
    }
    if (view == CALL_INTERFACE_IDLE) {
        unmapOverlay();
        shownView = CALL_INTERFACE_IDLE;
        return;
    }
    if (view == shownView && overlayMapped) return;
    readDisplaySize();
    if (!ensureOverlayWindow()) return;
    int height = windowHeight;
    int y = 0;
    if (view == CALL_INTERFACE_IN_CALL) {
        height = HANGUP_STRIP_HEIGHT;
        y = windowHeight - height;
    }
    mapOverlay(y, height);
    shownView = view;
    layoutButtons();
}

#ifdef __linux__
static int ignoreXErrors(Display*, XErrorEvent*) {
    return 0;
}
#endif

static void readDisplaySize() {
#ifdef __linux__
    if (!overlayDisplay) overlayDisplay = XOpenDisplay(NULL);
    if (overlayDisplay) {
        int screen = DefaultScreen(overlayDisplay);
        windowWidth = DisplayWidth(overlayDisplay, screen);
        windowHeight = DisplayHeight(overlayDisplay, screen);
        if (windowWidth > 0 && windowHeight > 0) return;
    }
#endif
    SDL_DisplayMode displayMode;
    if (SDL_GetDesktopDisplayMode(0, &displayMode) == 0) {
        windowWidth = displayMode.w;
        windowHeight = displayMode.h;
    }
}

static bool ensureOverlayWindow() {
    if (callWindow) return true;
#ifdef __linux__
    if (!overlayDisplay) overlayDisplay = XOpenDisplay(NULL);
    if (overlayDisplay) {
        XSetErrorHandler(ignoreXErrors);
        int screen = DefaultScreen(overlayDisplay);
        if (windowWidth <= 0 || windowHeight <= 0) {
            windowWidth = DisplayWidth(overlayDisplay, screen);
            windowHeight = DisplayHeight(overlayDisplay, screen);
        }
        overlayXWindow = XCreateSimpleWindow(overlayDisplay, RootWindow(overlayDisplay, screen), 0, 0, windowWidth, windowHeight, 0, BlackPixel(overlayDisplay, screen), WhitePixel(overlayDisplay, screen));
        XSetWindowAttributes attributes;
        attributes.override_redirect = True;
        attributes.event_mask = ExposureMask | ButtonPressMask | ButtonReleaseMask | StructureNotifyMask;
        XChangeWindowAttributes(overlayDisplay, overlayXWindow, CWOverrideRedirect | CWEventMask, &attributes);
        XStoreName(overlayDisplay, overlayXWindow, "Teleport call");
        XSync(overlayDisplay, False);
        callWindow = SDL_CreateWindowFrom(reinterpret_cast<void*>(static_cast<uintptr_t>(overlayXWindow)));
        if (!callWindow) cout << "Call interface SDL foreign window failed: " << SDL_GetError() << endl;
    }
#endif
    if (!callWindow) {
        callWindow = SDL_CreateWindow("Teleport call", 0, 0, windowWidth, windowHeight, SDL_WINDOW_BORDERLESS | SDL_WINDOW_HIDDEN | SDL_WINDOW_SKIP_TASKBAR);
        if (!callWindow) {
            cout << "Call interface window failed: " << SDL_GetError() << endl;
            return false;
        }
    }
    callRenderer = SDL_CreateRenderer(callWindow, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!callRenderer) {
        cout << "Call interface renderer failed: " << SDL_GetError() << endl;
        SDL_DestroyWindow(callWindow);
        callWindow = nullptr;
        return false;
    }
    SDL_ShowCursor(SDL_DISABLE);
    return true;
}

static void mapOverlay(int y, int height) {
#ifdef __linux__
    if (overlayDisplay && overlayXWindow) {
        XMoveResizeWindow(overlayDisplay, overlayXWindow, 0, y, windowWidth, height);
        XMapRaised(overlayDisplay, overlayXWindow);
        XGrabPointer(overlayDisplay, overlayXWindow, True, ButtonPressMask | ButtonReleaseMask, GrabModeAsync, GrabModeAsync, None, None, CurrentTime);
        XFlush(overlayDisplay);
        overlayMapped = true;
        return;
    }
#endif
    SDL_SetWindowSize(callWindow, windowWidth, height);
    SDL_SetWindowPosition(callWindow, 0, y);
    SDL_ShowWindow(callWindow);
    SDL_RaiseWindow(callWindow);
    overlayMapped = true;
}

static void unmapOverlay() {
    if (!overlayMapped) return;
#ifdef __linux__
    if (overlayDisplay && overlayXWindow) {
        XUngrabPointer(overlayDisplay, CurrentTime);
        XUnmapWindow(overlayDisplay, overlayXWindow);
        XFlush(overlayDisplay);
        overlayMapped = false;
        return;
    }
#endif
    if (callWindow) SDL_HideWindow(callWindow);
    overlayMapped = false;
}

static void destroyOverlayWindow() {
    overlayMapped = false;
    if (callRenderer) {
        SDL_DestroyRenderer(callRenderer);
        callRenderer = nullptr;
    }
    if (callWindow) {
        SDL_DestroyWindow(callWindow);
        callWindow = nullptr;
    }
#ifdef __linux__
    if (overlayDisplay && overlayXWindow) {
        XDestroyWindow(overlayDisplay, overlayXWindow);
        overlayXWindow = 0;
    }
    if (overlayDisplay) {
        XCloseDisplay(overlayDisplay);
        overlayDisplay = nullptr;
    }
#endif
    shownView = CALL_INTERFACE_IDLE;
}

static void layoutButtons() {
    int height = windowHeight;
    if (shownView == CALL_INTERFACE_IN_CALL) height = HANGUP_STRIP_HEIGHT;
    backButton = {20, 20, windowWidth - 40, CALL_BUTTON_HEIGHT};
    hangupButton = {0, 0, windowWidth, height};
    acceptButton = {20, height / 2, windowWidth - 40, CALL_BUTTON_HEIGHT};
    declineButton = {20, height / 2 + CALL_BUTTON_HEIGHT + 16, windowWidth - 40, CALL_BUTTON_HEIGHT};
    peerButtons.clear();
    int top = 20 + CALL_BUTTON_HEIGHT + 16;
    lock_guard<mutex> lock(callInterfaceMutex);
    for (size_t index = 0; index < callPeers.size(); index++) {
        peerButtons.push_back({20, top + static_cast<int>(index) * (CALL_ROW_HEIGHT + 12), windowWidth - 40, CALL_ROW_HEIGHT});
    }
}

static void drawOverlay() {
    if (!callRenderer) return;
    layoutButtons();
    CallInterfaceView view;
    vector<string> peers;
    string incoming;
    string status;
    {
        lock_guard<mutex> lock(callInterfaceMutex);
        view = requestedView;
        peers = callPeers;
        incoming = incomingPeer;
        status = callStatus;
    }
    SDL_Color white = {255, 255, 255, 255};
    SDL_Color black = {0, 0, 0, 255};
    SDL_Color blue = {40, 90, 180, 255};
    SDL_Color green = {30, 140, 70, 255};
    SDL_Color red = {180, 40, 40, 255};
    SDL_Color gray = {60, 60, 60, 255};
    SDL_SetRenderDrawColor(callRenderer, 245, 245, 245, 255);
    SDL_RenderClear(callRenderer);
    if (view == CALL_INTERFACE_DIRECTORY) {
        drawButton(backButton, "Close", gray, white);
        for (size_t index = 0; index < peers.size() && index < peerButtons.size(); index++) {
            drawButton(peerButtons[index], peers[index], blue, white);
        }
        if (peers.empty()) drawLabel(status.empty() ? "No peers online" : status.c_str(), 30, 20 + CALL_BUTTON_HEIGHT + 20, black);
        else if (!status.empty()) drawLabel(status.c_str(), 30, windowHeight - 60, black);
    } else if (view == CALL_INTERFACE_INCOMING) {
        string title = incoming.empty() ? "Incoming call" : "Incoming call " + incoming;
        drawLabel(title.c_str(), 30, windowHeight / 2 - 60, black);
        drawButton(acceptButton, "Accept", green, white);
        drawButton(declineButton, "Decline", red, white);
    } else if (view == CALL_INTERFACE_IN_CALL) {
        drawButton(hangupButton, "Hang up", red, white);
    }
    SDL_RenderPresent(callRenderer);
}

static void drawButton(const SDL_Rect& rect, const string& label, SDL_Color fill, SDL_Color text) {
    SDL_SetRenderDrawColor(callRenderer, fill.r, fill.g, fill.b, fill.a);
    SDL_RenderFillRect(callRenderer, &rect);
    if (!callFont || label.empty()) return;
    SDL_Surface* surface = TTF_RenderText_Solid(callFont, label.c_str(), text);
    if (!surface) return;
    SDL_Texture* texture = SDL_CreateTextureFromSurface(callRenderer, surface);
    int x = rect.x + (rect.w - surface->w) / 2;
    int y = rect.y + (rect.h - surface->h) / 2;
    SDL_Rect dest = {x, y, surface->w, surface->h};
    SDL_RenderCopy(callRenderer, texture, NULL, &dest);
    SDL_FreeSurface(surface);
    SDL_DestroyTexture(texture);
}

static void drawLabel(const char* text, int x, int y, SDL_Color color) {
    if (!callFont || !text || !text[0]) return;
    SDL_Surface* surface = TTF_RenderText_Solid(callFont, text, color);
    if (!surface) return;
    SDL_Texture* texture = SDL_CreateTextureFromSurface(callRenderer, surface);
    SDL_Rect dest = {x, y, surface->w, surface->h};
    SDL_RenderCopy(callRenderer, texture, NULL, &dest);
    SDL_FreeSurface(surface);
    SDL_DestroyTexture(texture);
}

static void handleOverlayEvent(const SDL_Event& event) {
    int x = 0;
    int y = 0;
    bool tap = false;
    readTap(event, x, y, tap);
    if (tap) handleTap(x, y);
}

static void pollXTaps() {
#ifdef __linux__
    if (!overlayDisplay || !overlayMapped) return;
    while (XPending(overlayDisplay)) {
        XEvent xevent;
        XNextEvent(overlayDisplay, &xevent);
        if (xevent.type == ButtonPress) handleTap(xevent.xbutton.x, xevent.xbutton.y);
    }
#endif
}

static void handleTap(int x, int y) {
    layoutButtons();
    CallInterfaceView view;
    vector<string> peers;
    {
        lock_guard<mutex> lock(callInterfaceMutex);
        view = requestedView;
        peers = callPeers;
    }
    if (view == CALL_INTERFACE_DIRECTORY && hitRect(backButton, x, y)) {
        showCallIdle();
        return;
    }
    if (view == CALL_INTERFACE_DIRECTORY) {
        for (size_t index = 0; index < peers.size() && index < peerButtons.size(); index++) {
            if (hitRect(peerButtons[index], x, y)) {
                queueAction("call", peers[index]);
                return;
            }
        }
    }
    if (view == CALL_INTERFACE_INCOMING && hitRect(acceptButton, x, y)) {
        queueAction("accept", "");
        return;
    }
    if (view == CALL_INTERFACE_INCOMING && hitRect(declineButton, x, y)) {
        queueAction("decline", "");
        showCallIdle();
        return;
    }
    if (view == CALL_INTERFACE_IN_CALL && hitRect(hangupButton, x, y)) queueAction("hangup", "");
}

static bool hitRect(const SDL_Rect& rect, int x, int y) {
    return x >= rect.x && x < rect.x + rect.w && y >= rect.y && y < rect.y + rect.h;
}

static void queueAction(string command, string peer) {
    lock_guard<mutex> lock(callInterfaceMutex);
    pendingAction = command;
    pendingPeer = peer;
}

static void readTap(const SDL_Event& event, int& x, int& y, bool& tap) {
    if (event.type == SDL_MOUSEBUTTONDOWN && event.button.button == SDL_BUTTON_LEFT) {
        x = event.button.x;
        y = event.button.y;
        tap = true;
        return;
    }
    if (event.type != SDL_FINGERDOWN) return;
    int width = windowWidth;
    int height = windowHeight;
    if (callWindow) SDL_GetWindowSize(callWindow, &width, &height);
    x = static_cast<int>(event.tfinger.x * width);
    y = static_cast<int>(event.tfinger.y * height);
    tap = true;
}
