// Record the camera and the microphone to an mp4 file, ffmpeg does the capture and the muxing.

// Local
#include "recorder.h"

// System
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// Posix
#include <errno.h>
#include <signal.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

// Namespace
using namespace std;
using namespace std::chrono;

// What the camera is asked for, the file lands at this size and rate
static const char* RECORD_VIDEO_SIZE = "1920x1080";
static const char* RECORD_FRAMERATE = "30";
static const char* RECORD_INPUT_FORMAT = "mjpeg";

// Mirror the file as it is written, so a playback matches the preview it was watched on
static const char* RECORD_FLIP = "hflip";

// Software H264, the Pi has no encoder in hardware, and quality against file size
static const char* RECORD_VIDEO_CODEC = "libx264";
static const char* RECORD_PRESET = "ultrafast";
static const char* RECORD_QUALITY = "26";
static const char* RECORD_PIXEL_FORMAT = "yuv420p";
static const char* RECORD_AUDIO_CODEC = "aac";

// Stop on its own after this long, so a recording left running cannot fill the card
static const char* RECORD_MAX_SECONDS = "3600";

// Refuse to start without this much room, an hour at this quality needs about two
static const long long RECORD_FREE_BYTES_NEEDED = 2LL * 1024 * 1024 * 1024;

// Give ffmpeg this long to close the file cleanly after the stop signal
static const int RECORD_STOP_WAIT_MS = 5000;
static const int RECORD_STOP_POLL_MS = 100;

// Second output ffmpeg sends back down a pipe, small and slow enough to watch and track on
static const int PREVIEW_WIDTH = 640;
static const int PREVIEW_HEIGHT = 360;
static const char* PREVIEW_SIZE = "640x360";
static const char* PREVIEW_FRAMERATE = "30";
static const char* PREVIEW_PIXEL_FORMAT = "bgr24";

// Read the microphone through the software mixer, so talk can keep listening
static const char* SHARED_CAPTURE_PREFIX = "plug:\"dsnoop:";
static const char* SHARED_CAPTURE_SUFFIX = ",0\"";

// Where the sound cards are listed, and the name a USB card carries there
static const char* SOUND_CARDS_PATH = "/proc/asound/cards";
static const char* USB_CARD_MARKER = "USB-Audio";

// The ffmpeg child, and when it started
static pid_t recorderPid = -1;
static steady_clock::time_point recorderStart;
static string recorderPath;

// The frames coming back from ffmpeg, and the thread that reads them
static thread previewThread;
static int previewFd = -1;
static mutex previewMutex;
static cv::Mat previewFrame;
static bool previewFrameIsNew = false;

static string recordingFilePath(const string& directory);
static string shortPath(const string& path);
static void readPreviewFrames();
static bool readExactly(int fd, unsigned char* buffer, size_t wanted);
static void stopPreviewReader();
static string microphoneDevice();
static int microphoneCard();
static bool cardCanCapture(int card);
static bool cardCanPlay(int card);
static string cardStreamInfo(int card);
static bool enoughRoom(const string& directory);

// Start ffmpeg on the camera and the microphone, the file grows until stop
bool start_recording(const string& directory, int cameraIndex) {
    if (recorderPid > 0) {
        cout << "Already recording" << endl;
        return false;
    }

    // Make the folder, and leave the card enough room to keep working
    error_code error;
    filesystem::create_directories(directory, error);
    if (!enoughRoom(directory))
        return false;

    // Name the camera, the microphone, and the file this recording writes
    string cameraDevice = "/dev/video" + to_string(cameraIndex);
    string microphone = microphoneDevice();
    string path = recordingFilePath(directory);

    // Build the ffmpeg arguments, wallclock timestamps keep the shared microphone in step
    vector<string> arguments = {
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "v4l2", "-input_format", RECORD_INPUT_FORMAT, "-video_size", RECORD_VIDEO_SIZE, "-framerate", RECORD_FRAMERATE, "-i", cameraDevice,
        "-f", "alsa", "-ac", "1", "-use_wallclock_as_timestamps", "1", "-i", microphone,
        "-map", "0:v", "-map", "1:a", "-vf", RECORD_FLIP,
        "-c:v", RECORD_VIDEO_CODEC, "-preset", RECORD_PRESET, "-crf", RECORD_QUALITY, "-pix_fmt", RECORD_PIXEL_FORMAT,
        "-c:a", RECORD_AUDIO_CODEC, "-t", RECORD_MAX_SECONDS, "-y", path,
        "-map", "0:v", "-s", PREVIEW_SIZE, "-r", PREVIEW_FRAMERATE, "-f", "rawvideo", "-pix_fmt", PREVIEW_PIXEL_FORMAT, "pipe:1"
    };

    // Open the pipe those small frames come back on
    int previewPipe[2];
    if (pipe(previewPipe) != 0) {
        perror("pipe preview");
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
        close(previewPipe[0]);
        close(previewPipe[1]);
        return false;
    }
    if (pid == 0) {
        // Raw frames go out on stdout, so that end of the pipe becomes stdout
        close(previewPipe[0]);
        dup2(previewPipe[1], STDOUT_FILENO);
        close(previewPipe[1]);
        execvp(commandLine[0], commandLine.data());
        cerr << "Error: ffmpeg: " << strerror(errno) << endl;
        _exit(1);
    }

    // Remember the child so the timer, the label, and the stop can find it
    close(previewPipe[1]);
    previewFd = previewPipe[0];
    recorderPid = pid;
    recorderStart = steady_clock::now();
    recorderPath = path;

    // Keep the pipe drained, ffmpeg stalls on a full one
    previewThread = thread(readPreviewFrames);
    cout << "Recording to " << shortPath(path) << endl;
    return true;
}

// Pull frames off the pipe for as long as ffmpeg sends them
static void readPreviewFrames() {
    size_t frameBytes = static_cast<size_t>(PREVIEW_WIDTH) * PREVIEW_HEIGHT * 3;
    cv::Mat frame(PREVIEW_HEIGHT, PREVIEW_WIDTH, CV_8UC3);
    while (readExactly(previewFd, frame.data, frameBytes)) {
        lock_guard<mutex> lock(previewMutex);
        frame.copyTo(previewFrame);
        previewFrameIsNew = true;
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
bool take_recording_frame(cv::Mat& frame) {
    lock_guard<mutex> lock(previewMutex);
    if (!previewFrameIsNew)
        return false;
    previewFrame.copyTo(frame);
    previewFrameIsNew = false;
    return true;
}

// Name a recording by its file alone, the folder is noise on the status bar
static string shortPath(const string& path) {
    return filesystem::path(path).filename().string();
}

// Name the file after the time it started, so recordings sort in order
static string recordingFilePath(const string& directory) {
    time_t now = time(nullptr);
    tm parts{};
    localtime_r(&now, &parts);
    char stamp[32];
    strftime(stamp, sizeof(stamp), "%Y_%m_%d_%H_%M_%S", &parts);
    return directory + "/video_" + stamp + ".mp4";
}

// Name the shared capture device for the USB microphone
static string microphoneDevice() {
    return SHARED_CAPTURE_PREFIX + to_string(microphoneCard()) + SHARED_CAPTURE_SUFFIX;
}

// Find the USB dongle, a camera card records nothing worth keeping
static int microphoneCard() {
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

    // Prefer a card that records and plays, that is the dongle rather than the camera
    for (int card : usbCards) {
        if (cardCanCapture(card) && cardCanPlay(card))
            return card;
    }

    // Otherwise take anything that can record at all
    for (int card : usbCards) {
        if (cardCanCapture(card))
            return card;
    }
    return 0;
}

// True when a card has a capture stream
static bool cardCanCapture(int card) {
    return cardStreamInfo(card).find("Capture:") != string::npos;
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

// True when the card has room for a recording, with a message when it does not
static bool enoughRoom(const string& directory) {
    error_code error;
    filesystem::space_info space = filesystem::space(directory, error);
    if (error)
        return true;
    if (static_cast<long long>(space.available) >= RECORD_FREE_BYTES_NEEDED)
        return true;
    cout << "Not enough disk space to record, " << space.available / (1024 * 1024) << " MB free" << endl;
    return false;
}

// Ask ffmpeg to finish, it writes the index that makes the file playable
void stop_recording() {
    if (recorderPid <= 0)
        return;

    // Interrupt is how ffmpeg is told to wrap up, a kill would leave a broken file
    double seconds = recording_seconds();
    kill(recorderPid, SIGINT);

    // Wait for it to close the file
    int status = 0;
    int waited = 0;
    while (waited < RECORD_STOP_WAIT_MS) {
        if (waitpid(recorderPid, &status, WNOHANG) == recorderPid) {
            recorderPid = -1;
            break;
        }
        usleep(RECORD_STOP_POLL_MS * 1000);
        waited += RECORD_STOP_POLL_MS;
    }

    // Out of patience, the file keeps whatever made it to disk
    if (recorderPid > 0) {
        cout << "ffmpeg did not stop, killing it, the recording may not play" << endl;
        kill(recorderPid, SIGKILL);
        waitpid(recorderPid, &status, 0);
        recorderPid = -1;
    }

    // Let the reader finish, the pipe ends when ffmpeg closes it
    stopPreviewReader();

    // Say what was written and how long it runs
    cout << "Recorded " << static_cast<int>(seconds) << " seconds to " << shortPath(recorderPath) << endl;
    recorderPath.clear();
}

// Wait for the reader to see the end of the pipe, then close it
static void stopPreviewReader() {
    if (previewThread.joinable())
        previewThread.join();
    if (previewFd >= 0) {
        close(previewFd);
        previewFd = -1;
    }

    // Drop the last frame, it belongs to the recording that just ended
    lock_guard<mutex> lock(previewMutex);
    previewFrameIsNew = false;
}

// True while ffmpeg is still going, the menu thread reads this for the button label
bool recording() {
    return recorderPid > 0;
}

// Collect ffmpeg when the length cap or an error ended it on its own
void reap_recording() {
    if (recorderPid <= 0)
        return;
    int status = 0;
    if (waitpid(recorderPid, &status, WNOHANG) != recorderPid)
        return;

    // Say where it landed, a recording that ran to the cap is still a good file
    stopPreviewReader();
    cout << "Recording ended, saved " << shortPath(recorderPath) << endl;
    recorderPid = -1;
    recorderPath.clear();
}

// How long the current recording has been running
double recording_seconds() {
    if (recorderPid <= 0)
        return 0.0;
    return duration_cast<milliseconds>(steady_clock::now() - recorderStart).count() / 1000.0;
}
