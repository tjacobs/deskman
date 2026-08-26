#pragma once

#include <string>
#include <vector>

void startCallInterface();
void stopCallInterface();
void pollCallInterface();
bool toggleCallDirectory();
void showIncomingCall(std::string peer);
void showCallInProgress();
void showCallIdle();
bool isCallOverlayIdle();
void showCallFailed(std::string message);
void setCallPeers(const std::vector<std::string>& names);
bool takeCallAction(std::string& command, std::string& peer);
bool isWebPeer(const std::string& name);
bool isCallInterfaceReady();
