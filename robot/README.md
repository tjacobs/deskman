# Robot

Robot is the C++ program to show the face, control the servos, and do the camera face tracking. All the voice code is Python in `../talk`. Runs on a Jetson Orin Nano or a Raspberry Pi 5. It also builds on macOS.

## Compile

Needs CMake, a C++ compiler, OpenCV, SDL2, SDL2_image, SDL2_ttf, and GStreamer on Linux. The `./install.sh` script installs the SDL2 libraries, pkg-config, and i2c-tools. It does not install OpenCV or GStreamer. Get OpenCV from JetPack or `libopencv-dev`, and GStreamer from `../teleport/install.sh`.

```bash
./install.sh
mkdir build
cd build
cmake ..
make
```

## Run

```bash
cd build
./robot
```

Starts the face window, servos, camera tracking, and `talk/talk.py`. 


| Flag           | Does                                              |
| -------------- | ------------------------------------------------- |
| `--no-talk`    | Skip starting `talk.py`                           |
| `--servos`     | Sweep servos, scan IDs, then exit                 |
| `--no-servos`  | Relax servos and print positions, for servo setup |
| `--id OLD NEW` | Set a servo ID, OLD of 0 is every servo           |
| `--camera`     | Show the face tracking preview on screen          |
| `--no-camera`  | Open no camera, face tracking off                 |
| `--help`       | List the flags                                    |


## Config

The `config.json` file is read from the working directory, and written with defaults when missing. It holds `useCamera`, `faceTracking`, and the servo travel limits `pan_min`, `pan_max`, `tilt_min`, `tilt_max`, `hat_min`, `hat_max`, plus `hat_dir`. Servos only move once all six travel limits are present.

## Camera

When the robot opens a USB camera it sets any picture settings listed for that camera model in `CAMERA_MODELS` in `src/camera.cpp`, matched on the name `v4l2-ctl --info` reports. The settings stay on the camera, so teleport calls and recordings get them too. Controls a camera lacks are skipped, and values are clamped to its range.

The Arducam 1080P Low Light looks through the round head opening, and the dark ring makes auto exposure wash out the middle, so it gets no backlight compensation, more contrast, lower gamma, and more saturation. To tune a new camera, try values live with `v4l2-ctl -d /dev/video0 -c contrast=64`, then add a row for it.

Recordings ask for MJPEG 1920x1080 at 30 fps when the camera lists it, otherwise ffmpeg takes the camera's own mode.

## Menu

The button at the right end of the status bar opens a menu with Quiet, Listen, Move, Camera, Record, Videos, Mode, Audio, WiFi, Call, and Exit. Mode steps talk through Local, Cloud, and Realtime. Record saves videos into `recordings/`, and Videos plays them.

Other programs, such as talk and teleport, drive the head, camera, and menu over a Unix socket in the user runtime directory.

## System setup

```bash
./install_system.sh
```

Run once per machine, then reboot. It sets up the system: booting to graphical, auto-logs in, hides the pointer and crash and update dialogs, keeps the screen and Wi-Fi awake, fixes the mDNS name, rotates the screen to portrait, and adds Robot and Teleport desktop icons. On the Pi it also writes the DSI panel, servo UART, and I2C lines into `/boot/firmware/config.txt`, and installs a matchbox touch keyboard if not present. On the Jetson it turns on the screen keyboard if not on already.

The Robot icon runs `start_robot.sh`, which offers the OpenAI key setup when `talk/openai.env` has no key, then starts `robot.service`. The`restart_services.sh` script restarts robot and teleport after a short delay, so talk can restart them by voice.

## Service

```bash
./install_robot_service.sh
./install_robot_service.sh --start
./install_robot_service.sh --uninstall
```

This installs the robot service. The robot service runs `robot_service.sh`, which runs `build/robot`, or just `talk.py` when the robot is not built.

## Screen

The screen is a 1024x600 Waveshare touch screen, rotated left.

## Servos

The Waveshare STS3215 serial bus servos are at default speed 1 Mbps, and ID 1 is pan, 2 is tilt, and 3 is the hat. Servos at 115200 baud are found and moved to 1 Mbps. The bus is the first of `/dev/ttyUSB0, 1, 2`, `/dev/ttyACM0` and `1`, `/dev/ttyTHS1` on the Jetson, or `/dev/ttyAMA0` on the Pi.

On the Pi the bus is GPIO 14 (TX) and GPIO 15 (RX). Pi 5 leaves that UART off until `uart0-pi5` is loaded, which creates `/dev/ttyAMA0`. The`install_system.sh` script adds this to `/boot/firmware/config.txt`.

To read servo positions safely without moving anything:

```bash
cd build
./robot --no-servos
```

## Battery

Battery pack voltage and current come from an INA219 on I2C on the 40-pin header, GPIO 2 (SDA) and GPIO 3 (SCL). That is `/dev/i2c-7` on the Jetson and `/dev/i2c-1` on the Pi. Both 12V and 24V packs are recognised from the voltage. Pi OS leaves that bus off until this is in `/boot/firmware/config.txt`, which `install_system.sh` adds. Reboot after, then `i2cdetect -y 1` should show the chip at `0x40`. Without the meter, talk skips the low battery warnings.