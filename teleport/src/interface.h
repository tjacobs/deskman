#pragma once

#include <string>

void startInterface();
void stopInterface();
void pauseInterfaceForCall();
void resumeInterfaceAfterCall();
bool takeInterfaceCommand(std::string& command, std::string& peer);
bool getInterfaceBattery(float& voltage, int& percent, float& current);
bool isInterfaceConnected();
