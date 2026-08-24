// Deskman robot control socket.
// Local Unix socket so Python can move the head while C++ owns the servos.

#pragma once

// C++ standard library
#include <string>

// Start the Unix-socket control loop on a background thread
bool start_control(const std::string& socket_path = "/tmp/robot.socket");

// Stop control and remove the socket file
void stop_control();
