/*
 * Video
*/

#pragma once

#include "audio.h"
#include <string>
#include <ixwebsocket/IXWebSocket.h>

using namespace std;
using namespace ix;

void initVideoCamera(WebSocket* webSocketPointer, string deviceNameValue);
void checkVideoCamera(string cameraPath);
void startVideo(string cameraPath, string cameraView);
bool startCall(string cameraPath);
void startRemoteMic();
void stopRemoteMic();
void stopVideo();
bool isVideoRunning();
void handleVideoMessage(string command, string payload);
void clearVideoAddresses();
