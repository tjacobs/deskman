# Teleport

This is the video calling program, for calls robot to robot and web to robot. It logs into the teleportconnect.com websocket server at `ws://server.teleportconnect.com:8080` and handles WebRTC, video, and audio with GStreamer. Each robot logs in as `deskman` plus its device number.

Dragging your finger (on the deskman screen) or mouse (on web) steers the remote robot's head. Teleport talks to the robot over local unix socket `robot.interface` to move the head, grab the camera for a call, and open the call list when the menu Call button is tapped.

## Compile

Needs CMake, a C++ compiler, GStreamer WebRTC, json-glib, libnice, SDL2, SDL2_ttf, and X11 with Xext on Linux. The `./install.sh` script installs the requirements including the NVIDIA GStreamer packages on a Jetson.

```bash
./install.sh
mkdir -p build
cd build
cmake ..
make
```

## Echo cancel

```bash
./install_anti_echo.sh
```

Optional. Builds the newer AEC3 echo canceller and a `webrtcdsp` plugin into `/usr/local/lib/deskman-gstreamer-1.0`. Without it calls use the older system AEC2, or no echo cancel when `webrtcdsp` is missing. The startup log says which one a call will get.

## Run

```bash
cd build
./teleport
```

`./teleport --help` lists flags.


| Flag            | Does                                        |
| --------------- | ------------------------------------------- |
| `--device N`    | Log in as `deskmanN`                        |
| `--call PEER`   | Call a peer after login, like `deskman2`    |
| `--mute`        | Start with the call mic muted, the default  |
| `--no-mute`     | Start with the call mic on                  |
| `--server URL`  | Use another websocket server                |
| `--local`       | Use a local server at `ws://127.0.0.1:8080` |
| `--camera PATH` | Camera path or index                        |
| `--audio NAME`  | ALSA mic and speaker device for calls       |


Set `GST_PLUGIN_PATH=/usr/local/lib/deskman-gstreamer-1.0` to pick up AEC3 echo cancel lib when running by hand. The service sets it.

## Settings

`config.json` is read from the working directory, and written with defaults on first run.

```json
{
  "deviceId": 4,
  "autoAnswer": 5
}
```

The `deviceId` defaults to the number on the end of the hostname. An incoming call rings the speaker and shows a bar along the bottom of the screen. The `autoAnswer` is how many seconds to ring before answering by itself, and `0` makes it ring until someone taps Accept or Decline.

## Service

```bash
./install_teleport_service.sh
./install_teleport_service.sh --start
./install_teleport_service.sh --uninstall
```

Run with no flag it installs and enables `teleport.service`, and `--start` also starts it now. The service runs `build/teleport` as the installing user with the AEC3 plugin path. The `start_teleport.sh` script does the same by hand from root, finding the built binary and running it as its owner.