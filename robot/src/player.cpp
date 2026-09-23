// Play a recording back in the robot window, ffmpeg does the decoding and the sound.

// Local
#include "player.h"
#include "screen.h"

// System
#include <algorithm>
#include <atomic>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <thread>

// Posix
#include <errno.h>
#include <signal.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

// Namespace
using namespace std;
using namespace std::chrono;

// Only these are offered for playback
static const char* RECORDING_SUFFIX = ".mp4";

// Frames come back at this size, matching the widescreen the camera records
static const int PLAY_WIDTH = 640;
static const int PLAY_HEIGHT = 360;

// The video sits where the camera preview does, below the top edge
static const int PLAY_TOP = 120;
static const char* PLAY_SIZE = "640x360";
static const char* PLAY_PIXEL_FORMAT = "bgr24";

// Play through the software mixer, so a playback cannot lock the speaker away from talk
static const char* SHARED_PLAYBACK_PREFIX = "plug:\"dmix:";
static const char* SHARED_PLAYBACK_SUFFIX = ",0\"";

// Where the sound cards are listed, and the name a USB card carries there
static const char* SOUND_CARDS_PATH = "/proc/asound/cards";
static const char* USB_CARD_MARKER = "USB-Audio";

// Ask ffprobe how long a file runs
static const char* PROBE_COMMAND = "ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 ";

// How the date beside each recording reads
static const char* DATE_FORMAT = "%b %d %l:%M %p";

// Give ffmpeg this long to stop before it is killed
static const int PLAY_STOP_WAIT_MS = 2000;
static const int PLAY_STOP_POLL_MS = 50;

// The ffmpeg child, and the file it is playing
static pid_t playerPid = -1;
static string playerPath;

// How long each recording runs, asking ffprobe is slow so the answers are kept
static map<string, double> lengthCache;
static mutex lengthMutex;
static atomic<bool> lengthsProbing{false};
static atomic<bool> lengthsUpdated{false};

// What the video is drawn with, kept between frames
static SDL_Texture* playTexture = nullptr;
static cv::Mat playRgb;

// The frames coming back from ffmpeg, and the thread that reads them
static thread playThread;
static int playFd = -1;
static mutex playMutex;
static cv::Mat playFrame;
static bool playFrameIsNew = false;

static double cachedLength(const string& path);
static void probeLengths(const vector<Recording>& recordings);
static double recordingLength(const string& path);
static string recordingDate(const filesystem::path& path);
static void readPlaybackFrames();
static bool readExactly(int fd, unsigned char* buffer, size_t wanted);
static void stopPlaybackReader();
static string speakerDevice();
static int speakerCard();
static bool cardCanPlay(int card);
static string cardStreamInfo(int card);

// Gather the recordings on disk, newest first, without waiting on their lengths
vector<Recording> list_recordings(const string& directory) {
    vector<Recording> recordings;

    // Walk the folder, keeping the video files
    error_code error;
    for (const filesystem::directory_entry& entry : filesystem::directory_iterator(directory, error)) {
        if (!entry.is_regular_file() || entry.path().extension() != RECORDING_SUFFIX)
            continue;
        Recording recording;
        recording.path = entry.path().string();
        recording.dateText = recordingDate(entry.path());
        recording.seconds = cachedLength(recording.path);
        recording.bytes = static_cast<long long>(entry.file_size(error));
        recordings.push_back(recording);
    }

    // Newest first, the file names carry the time they started
    sort(recordings.begin(), recordings.end(), [](const Recording& left, const Recording& right) {
        return left.path > right.path;
    });

    // Go and measure whatever has not been measured yet
    probeLengths(recordings);
    return recordings;
}

// The length already known for a recording, or none yet
static double cachedLength(const string& path) {
    lock_guard<mutex> lock(lengthMutex);
    auto found = lengthCache.find(path);
    return found == lengthCache.end() ? 0.0 : found->second;
}

// Measure the recordings that have no length yet, off the main thread
static void probeLengths(const vector<Recording>& recordings) {
    if (lengthsProbing.load())
        return;

    // Collect what is missing, ffprobe takes long enough to be felt on a tap
    vector<string> missing;
    for (const Recording& recording : recordings) {
        if (recording.seconds <= 0.0)
            missing.push_back(recording.path);
    }
    if (missing.empty())
        return;

    // Measure them one by one, saying so once they are all done
    lengthsProbing = true;
    thread([missing] {
        for (const string& path : missing) {
            double seconds = recordingLength(path);
            lock_guard<mutex> lock(lengthMutex);
            lengthCache[path] = seconds;
        }
        lengthsUpdated = true;
        lengthsProbing = false;
    }).detach();
}

// True once after the lengths have been measured, so the list can be redrawn
bool recording_lengths_updated() {
    return lengthsUpdated.exchange(false);
}

// Ask ffprobe how many seconds a recording runs
static double recordingLength(const string& path) {
    string command = PROBE_COMMAND + ("\"" + path + "\"");
    FILE* probe = popen(command.c_str(), "r");
    if (!probe)
        return 0.0;

    // Read the one number it prints
    char answer[64] = {0};
    if (!fgets(answer, sizeof(answer), probe)) {
        pclose(probe);
        return 0.0;
    }
    pclose(probe);
    return atof(answer);
}

// Say when a recording was written
static string recordingDate(const filesystem::path& path) {
    error_code error;
    filesystem::file_time_type written = filesystem::last_write_time(path, error);
    if (error)
        return "";

    // Turn the file clock into a wall clock the user recognises
    auto systemTime = time_point_cast<system_clock::duration>(written - filesystem::file_time_type::clock::now() + system_clock::now());
    time_t when = system_clock::to_time_t(systemTime);
    tm parts{};
    localtime_r(&when, &parts);
    char text[32];
    strftime(text, sizeof(text), DATE_FORMAT, &parts);

    // The hour is padded to two places, so single digit times carry a gap
    string date = text;
    size_t gap = date.find("  ");
    if (gap != string::npos)
        date.erase(gap, 1);
    return date;
}

// Remove a recording, it is gone for good
bool delete_recording(const string& path) {
    error_code error;
    if (!filesystem::remove(path, error)) {
        cout << "Could not delete " << recording_name(path) << endl;
        return false;
    }
    cout << "Deleted " << recording_name(path) << endl;
    return true;
}

// Start ffmpeg on a recording, video comes back on a pipe and sound goes to the speaker
bool start_playback(const string& path) {
    if (playerPid > 0)
        stop_playback();

    // Read at the speed it was recorded, so it plays rather than races
    vector<string> arguments = {
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-re", "-i", path,
        "-map", "0:v", "-s", PLAY_SIZE, "-f", "rawvideo", "-pix_fmt", PLAY_PIXEL_FORMAT, "pipe:1",
        "-map", "0:a?", "-f", "alsa", speakerDevice()
    };

    // Open the pipe the frames come back on
    int playPipe[2];
    if (pipe(playPipe) != 0) {
        perror("pipe playback");
        return false;
    }

    // Hand the strings to exec as a plain array
    vector<char*> commandLine;
    for (string& argument : arguments)
        commandLine.push_back(const_cast<char*>(argument.c_str()));
    commandLine.push_back(nullptr);

    // Run it, its own messages land in the robot log
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork ffmpeg");
        close(playPipe[0]);
        close(playPipe[1]);
        return false;
    }
    if (pid == 0) {
        // Raw frames go out on stdout, so that end of the pipe becomes stdout
        close(playPipe[0]);
        dup2(playPipe[1], STDOUT_FILENO);
        close(playPipe[1]);
        execvp(commandLine[0], commandLine.data());
        cerr << "Error: ffmpeg: " << strerror(errno) << endl;
        _exit(1);
    }

    // Remember the child so the window and the stop can find it
    close(playPipe[1]);
    playFd = playPipe[0];
    playerPid = pid;
    playerPath = path;

    // Keep the pipe drained, ffmpeg stalls on a full one
    playThread = thread(readPlaybackFrames);
    cout << "Playing " << recording_name(path) << endl;
    return true;
}

// Pull frames off the pipe for as long as ffmpeg sends them
static void readPlaybackFrames() {
    size_t frameBytes = static_cast<size_t>(PLAY_WIDTH) * PLAY_HEIGHT * 3;
    cv::Mat frame(PLAY_HEIGHT, PLAY_WIDTH, CV_8UC3);
    while (readExactly(playFd, frame.data, frameBytes)) {
        lock_guard<mutex> lock(playMutex);
        frame.copyTo(playFrame);
        playFrameIsNew = true;
    }
}

// Fill the buffer or say the pipe ended
static bool readExactly(int fd, unsigned char* buffer, size_t wanted) {
    size_t filled = 0;
    while (filled < wanted) {
        ssize_t piece = read(fd, buffer + filled, wanted - filled);
        if (piece <= 0)
            return false;
        filled += static_cast<size_t>(piece);
    }
    return true;
}

// The newest frame ffmpeg sent, each one handed out once
bool take_playback_frame(cv::Mat& frame) {
    lock_guard<mutex> lock(playMutex);
    if (!playFrameIsNew)
        return false;
    playFrame.copyTo(frame);
    playFrameIsNew = false;
    return true;
}

// Show the video where the camera preview sits
void draw_playback_frame() {
    if (!renderer)
        return;

    // Keep showing the last frame between the ones ffmpeg sends
    cv::Mat frame;
    if (take_playback_frame(frame)) {
        cv::cvtColor(frame, playRgb, cv::COLOR_BGR2RGB);
        if (!playTexture)
            playTexture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, PLAY_WIDTH, PLAY_HEIGHT);
        if (playTexture)
            SDL_UpdateTexture(playTexture, NULL, playRgb.data, static_cast<int>(playRgb.step));
    }
    if (!playTexture)
        return;

    // Across the full width, keeping the shape of the recording
    int height = (screen_width * PLAY_HEIGHT) / PLAY_WIDTH;
    SDL_Rect where = {0, PLAY_TOP, screen_width, height};
    SDL_RenderCopy(renderer, playTexture, NULL, &where);
}

// Stop a playback part way through
void stop_playback() {
    if (playerPid <= 0)
        return;

    // Ask it to quit, then insist if it will not
    kill(playerPid, SIGINT);
    int status = 0;
    int waited = 0;
    while (waited < PLAY_STOP_WAIT_MS) {
        if (waitpid(playerPid, &status, WNOHANG) == playerPid) {
            playerPid = -1;
            break;
        }
        usleep(PLAY_STOP_POLL_MS * 1000);
        waited += PLAY_STOP_POLL_MS;
    }
    if (playerPid > 0) {
        kill(playerPid, SIGKILL);
        waitpid(playerPid, &status, 0);
        playerPid = -1;
    }

    // Let the reader see the end of the pipe
    stopPlaybackReader();
    cout << "Stopped playing " << recording_name(playerPath) << endl;
    playerPath.clear();
}

// Collect ffmpeg when the recording played to its end
void close_playback() {
    if (playerPid <= 0)
        return;
    int status = 0;
    if (waitpid(playerPid, &status, WNOHANG) != playerPid)
        return;
    stopPlaybackReader();
    cout << "Finished playing " << recording_name(playerPath) << endl;
    playerPid = -1;
    playerPath.clear();
}

// Wait for the reader to see the end of the pipe, then close it
static void stopPlaybackReader() {
    if (playThread.joinable())
        playThread.join();
    if (playFd >= 0) {
        close(playFd);
        playFd = -1;
    }

    // Drop the last frame, it belongs to the playback that just ended
    lock_guard<mutex> lock(playMutex);
    playFrameIsNew = false;
}

// True while a recording is playing
bool playing() {
    return playerPid > 0;
}

// Name the shared mixer playback device for the USB speaker
static string speakerDevice() {
    return SHARED_PLAYBACK_PREFIX + to_string(speakerCard()) + SHARED_PLAYBACK_SUFFIX;
}

// Find a USB card that can play, a camera card would play into nothing
static int speakerCard() {
    // Read the card list, keeping the number from every USB line
    vector<int> usbCards;
    ifstream cardsFile(SOUND_CARDS_PATH);
    string line;
    while (getline(cardsFile, line)) {
        if (line.find(USB_CARD_MARKER) == string::npos)
            continue;
        size_t start = line.find_first_not_of(" \t");
        if (start != string::npos)
            usbCards.push_back(atoi(line.c_str() + start));
    }

    // Take the first one with a playback stream
    for (int card : usbCards) {
        if (cardCanPlay(card))
            return card;
    }
    return 0;
}

// True when a card has a playback stream
static bool cardCanPlay(int card) {
    return cardStreamInfo(card).find("Playback:") != string::npos;
}

// Read what a USB card says it can do
static string cardStreamInfo(int card) {
    ifstream streamFile("/proc/asound/card" + to_string(card) + "/stream0");
    if (!streamFile)
        return "";
    return string((istreambuf_iterator<char>(streamFile)), istreambuf_iterator<char>());
}

// Name a recording by its file alone
string recording_name(const string& path) {
    return filesystem::path(path).filename().string();
}

// Say a length as minutes and seconds
string length_text(double seconds) {
    int whole = static_cast<int>(seconds);
    char text[32];
    snprintf(text, sizeof(text), "%d:%02d", whole / 60, whole % 60);
    return text;
}

// Say a size in megabytes
string size_text(long long bytes) {
    char text[32];
    snprintf(text, sizeof(text), "%lld MB", bytes / (1024 * 1024));
    return text;
}
