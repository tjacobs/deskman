/*
 * Look
*/

#pragma once

#include <string>
#include <ixwebsocket/IXWebSocket.h>

using namespace std;
using namespace ix;

void initLook(WebSocket* webSocketPointer, string deviceNameValue);
void pollLook();
void stopLook();
