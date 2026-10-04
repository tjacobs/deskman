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

// Join a network, a new secure one asks for its password on screen
void connect_network(const Network& network);

// Forget a saved network, dropping it if joined
void forget_network(const Network& network);

// True while a look or a join is still going, and true only for a join
bool wifi_busy();
bool wifi_joining();

// The network being joined, empty when no join is going
string wifi_joining_name();
