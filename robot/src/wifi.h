#pragma once

// System
#include <string>
#include <vector>

// Namespace
using namespace std;

// One wireless network, as the list shows it
struct Network {
    string name;
    int signal;
    bool active;
    bool saved;
};

// Look up what is connected and what is in range, in the background
void refresh_networks();

// What the robot is connected to, with its address
string wifi_status_text();

// The networks found by the last look, strongest first
vector<Network> wifi_networks();

// Join a network, one that has been set up before needs no password
void connect_network(const string& name);

// True while a look or a join is still going
bool wifi_busy();
