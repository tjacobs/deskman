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
#include <sys/wait.h>
#include <unistd.h>

// Namespace
using namespace std;

// Ask for the networks in range from a fresh scan, while connected the cached list can shrink to the joined network
static const char* LIST_COMMAND = "nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi list --rescan yes 2>/dev/null";

// How long a join waits, the system password prompt sits inside it while someone types
static const char* JOIN_WAIT_SECONDS = "300";
// What exec returns when the program could not start
static const int EXEC_FAILED = 127;

// Ask which connections have been set up, and when each one last joined
static const char* SAVED_COMMAND = "nmcli -t -f UUID,TYPE,TIMESTAMP connection show 2>/dev/null";

// What nmcli says for a connection that has never joined
static const char* NEVER_JOINED = "0";

// Ask for the address this machine answers on
static const char* ADDRESS_COMMAND = "hostname -I 2>/dev/null";

// Only wireless connections carry a network name
static const char* WIRELESS_TYPE = "802-11-wireless";

// What the header says while there is nothing to report yet, and before the joined network
static const char* STATUS_LOOKING = "Looking...";
static const char* STATUS_OFFLINE = "Not connected";
static const char* STATUS_CONNECTED = "Connected: ";

// Longest line nmcli is expected to answer with
static const int COMMAND_LINE_SIZE = 512;

// What the last look found, and whether one is still running
static mutex wifiMutex;
static string wifiStatus = STATUS_LOOKING;
static vector<Network> wifiList;
static atomic<bool> wifiWorking{false};
static atomic<bool> wifiJoining{false};

// A connection this machine has set up, and whether it has ever joined
struct SavedConnection {
    string uuid;
    string ssid;
    bool joined;
};

static void readNetworks();
static vector<Network> listNetworks();
static vector<string> savedNetworkNames();
static vector<SavedConnection> savedConnections();
static string describeConnection(const vector<Network>& networks);
static void joinNetwork(const Network& network);
static vector<string> runCommand(const string& command);
static int runProgram(const vector<string>& arguments, vector<string>& lines);
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

// Read the network names this machine has joined before, a password that never worked does not count
static vector<string> savedNetworkNames() {
    vector<string> names;
    for (const SavedConnection& connection : savedConnections())
        if (connection.joined)
            names.push_back(connection.ssid);
    return names;
}

// Read every wireless connection, the network it joins, and whether it ever has
static vector<SavedConnection> savedConnections() {
    vector<SavedConnection> connections;
    for (const string& line : runCommand(SAVED_COMMAND)) {
        // Each line is the connection id, its type, then when it last joined
        vector<string> fields = splitFields(line);
        if (fields.size() < 3 || fields[1] != WIRELESS_TYPE)
            continue;

        // The connection name is not the network name, so ask for that too
        vector<string> ssid;
        if (runProgram({"nmcli", "-t", "-g", "802-11-wireless.ssid", "connection", "show", "uuid", fields[0]}, ssid) != 0 || ssid.empty() || ssid[0].empty())
            continue;
        connections.push_back({fields[0], ssid[0], fields[2] != NEVER_JOINED});
    }
    return connections;
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
    return STATUS_CONNECTED + active->name + " " + to_string(active->signal) + "%   " + where;
}

// Join a network, nmcli reuses the saved password, and a new secure one asks for it on screen
void connect_network(const Network& network) {
    if (wifiWorking.load())
        return;

    // Joining takes a few seconds and the password box waits on a person, so it runs off the main thread
    wifiWorking = true;
    wifiJoining = true;
    thread([network] {
        joinNetwork(network);
        wifiJoining = false;
        wifiWorking = false;
        refresh_networks();
    }).detach();
}

// Join, the system asks for a new network's password with its keyboard, and forget a new network that would not join
static void joinNetwork(const Network& network) {
    // Join, waiting long enough for the password to be typed
    vector<string> output;
    int result = runProgram({"nmcli", "--wait", JOIN_WAIT_SECONDS, "device", "wifi", "connect", network.name}, output);

    // Joined, so the connection stays saved
    if (result == 0)
        return;

    // Log why it failed so the status bar shows it
    for (const string& line : output)
        cout << line << endl;

    // Forget any connection to this network that has never joined, so a wrong or missing password is not kept
    for (const SavedConnection& connection : savedConnections()) {
        if (connection.joined || connection.ssid != network.name)
            continue;
        vector<string> deleted;
        runProgram({"nmcli", "connection", "delete", "uuid", connection.uuid}, deleted);
        for (const string& line : deleted)
            cout << line << endl;
    }
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

// True while a join is running, the password box included
bool wifi_joining() {
    return wifiJoining.load();
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

// Run a program with its own arguments and no shell, so a network name is never parsed, and return its exit code
static int runProgram(const vector<string>& arguments, vector<string>& lines) {
    int pipe_ends[2];
    if (pipe(pipe_ends) != 0)
        return EXEC_FAILED;

    // Child sends its output and errors down the pipe
    pid_t pid = fork();
    if (pid == 0) {
        dup2(pipe_ends[1], STDOUT_FILENO);
        dup2(pipe_ends[1], STDERR_FILENO);
        close(pipe_ends[0]);
        close(pipe_ends[1]);
        vector<char*> argv;
        for (const string& argument : arguments)
            argv.push_back(const_cast<char*>(argument.c_str()));
        argv.push_back(nullptr);
        execvp(argv[0], argv.data());
        _exit(EXEC_FAILED);
    }
    close(pipe_ends[1]);
    if (pid < 0) {
        close(pipe_ends[0]);
        return EXEC_FAILED;
    }

    // Keep each line without its newline
    FILE* output = fdopen(pipe_ends[0], "r");
    char line[COMMAND_LINE_SIZE];
    while (output && fgets(line, sizeof(line), output)) {
        string text = line;
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r'))
            text.pop_back();
        lines.push_back(text);
    }
    if (output)
        fclose(output);

    // Hand back how it exited
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : EXEC_FAILED;
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
