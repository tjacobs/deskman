#pragma once

#include <string>

void startInterface();
void stopInterface();
void pauseInterfaceForCall();
void resumeInterfaceAfterCall();
bool takeInterfaceCommand(std::string& command, std::string& peer);
bool isInterfaceConnected();
