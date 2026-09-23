// Read and change the wireless connection, nmcli does the work.

// Local
#include "wifi.h"

// System
#include <algorithm>
#include <atomic>
#include <iostream>
#include <mutex>
#include <thread>

// Posix
#include <stdio.h>

// Namespace
using namespace std;

// Ask for the networks in range, letting nmcli decide whether to scan again
static const char* LIST_COMMAND = "nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi list --rescan auto 2>/dev/null";

// Ask which connections have been set up, and what network each one joins
static const char* SAVED_COMMAND = "nmcli -t -f NAME,TYPE connection show 2>/dev/null";
static const char* SAVED_SSID_COMMAND = "nmcli -t -g 802-11-wireless.ssid connection show ";

// Ask for the address this machine answers on
static const char* ADDRESS_COMMAND = "hostname -I 2>/dev/null";

// Only wireless connections carry a network name
static const char* WIRELESS_TYPE = "802-11-wireless";

// What the header says while there is nothing to report yet
static const char* STATUS_LOOKING = "Looking...";
static const char* STATUS_OFFLINE = "Not connected";

// Longest line nmcli is expected to answer with
static const int COMMAND_LINE_SIZE = 512;

// What the last look found, and whether one is still running
static mutex wifiMutex;
static string wifiStatus = STATUS_LOOKING;
static vector<Network> wifiList;
static atomic<bool> wifiWorking{false};

static void readNetworks();
static vector<Network> listNetworks();
static vector<string> savedNetworkNames();
static string describeConnection(const vector<Network>& networks);
static vector<string> runCommand(const string& command);
static vector<string> splitFields(const string& line);

// Look up the networks without holding up the screen
void refresh_networks() {
    if (wifiWorking.load())
        return;
    wifiWorking = true;
    thread(readNetworks).detach();
}

// Gather the list and the header, then publish both at once
static void readNetworks() {
    vector<Network> networks = listNetworks();
    string status = describeConnection(networks);
    unique_lock<mutex> lock(wifiMutex);
    wifiList = networks;
    wifiStatus = status;
    lock.unlock();
    wifiWorking = false;
}

// Read the networks in range, strongest first and one row per name
static vector<Network> listNetworks() {
    vector<string> saved = savedNetworkNames();
    vector<Network> networks;
    for (const string& line : runCommand(LIST_COMMAND)) {
        // Each line is active, name, then signal
        vector<string> fields = splitFields(line);
        if (fields.size() < 3 || fields[1].empty())
            continue;
        Network network;
        network.name = fields[1];
        network.signal = atoi(fields[2].c_str());
        network.active = fields[0] == "yes";
        network.saved = find(saved.begin(), saved.end(), network.name) != saved.end();

        // A mesh shows the same name more than once, keep the strongest of them
        auto same = find_if(networks.begin(), networks.end(), [&](const Network& other) { return other.name == network.name; });
        if (same == networks.end()) {
            networks.push_back(network);
            continue;
        }
        same->active = same->active || network.active;
        if (network.signal > same->signal)
            same->signal = network.signal;
    }

    // Strongest first, with whatever is connected at the top
    sort(networks.begin(), networks.end(), [](const Network& left, const Network& right) {
        if (left.active != right.active)
            return left.active;
        return left.signal > right.signal;
    });
    return networks;
}

// Read the network names this machine already has a password for
static vector<string> savedNetworkNames() {
    vector<string> names;
    for (const string& line : runCommand(SAVED_COMMAND)) {
        // Each line is the connection name, then its type
        vector<string> fields = splitFields(line);
        if (fields.size() < 2 || fields[1] != WIRELESS_TYPE)
            continue;

        // The connection name is not the network name, so ask for that too
        vector<string> ssid = runCommand(SAVED_SSID_COMMAND + ("\"" + fields[0] + "\" 2>/dev/null"));
        if (!ssid.empty() && !ssid[0].empty())
            names.push_back(ssid[0]);
    }
    return names;
}

// Say what is connected, and where the robot can be reached
static string describeConnection(const vector<Network>& networks) {
    // Nothing joined, the list is all there is to show
    auto active = find_if(networks.begin(), networks.end(), [](const Network& network) { return network.active; });
    if (active == networks.end())
        return STATUS_OFFLINE;

    // Name, strength, and address on one line
    vector<string> address = runCommand(ADDRESS_COMMAND);
    string where = address.empty() ? "" : address[0];
    where = where.substr(0, where.find(' '));
    return active->name + "   " + to_string(active->signal) + "%   " + where;
}

// Join a network, nmcli reuses the saved password when there is one
void connect_network(const string& name) {
    if (wifiWorking.load())
        return;

    // Joining takes a few seconds, so it waits off the main thread
    wifiWorking = true;
    thread([name] {
        string command = "nmcli device wifi connect \"" + name + "\" 2>&1";
        for (const string& line : runCommand(command))
            cout << line << endl;
        wifiWorking = false;
        refresh_networks();
    }).detach();
}

// The header line for the list
string wifi_status_text() {
    lock_guard<mutex> lock(wifiMutex);
    return wifiStatus;
}

// The networks the last look found
vector<Network> wifi_networks() {
    lock_guard<mutex> lock(wifiMutex);
    return wifiList;
}

// True while a look or a join is running
bool wifi_busy() {
    return wifiWorking.load();
}

// Run a command and hand back its lines
static vector<string> runCommand(const string& command) {
    vector<string> lines;
    FILE* output = popen(command.c_str(), "r");
    if (!output)
        return lines;

    // Keep each line without its newline
    char line[COMMAND_LINE_SIZE];
    while (fgets(line, sizeof(line), output)) {
        string text = line;
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r'))
            text.pop_back();
        lines.push_back(text);
    }
    pclose(output);
    return lines;
}

// Split one nmcli line on its colons, a name can contain an escaped one
static vector<string> splitFields(const string& line) {
    vector<string> fields;
    string field;
    for (size_t index = 0; index < line.size(); index++) {
        // A backslash means the next character is part of the field
        if (line[index] == '\\' && index + 1 < line.size()) {
            field += line[index + 1];
            index++;
            continue;
        }
        if (line[index] == ':') {
            fields.push_back(field);
            field.clear();
            continue;
        }
        field += line[index];
    }
    fields.push_back(field);
    return fields;
}
