# Deskman

Deskman is a desktop robot: a face on a screen that looks around, with an interactive voice assistant, that can make video calls. It runs on a Jetson Orin Nano or a Raspberry Pi 5. The main parts are:

- [`talk`](talk/README.md) — Voice interaction. Wake word, speech to text, an LLM, and speech generation. Runs locally on Gemma, or on OpenAI when online. Written in Python.
- [`robot`](robot/README.md) — The face, servo control, camera face tracking, and the on-screen menu. Written in C++.
- [`teleport`](teleport/README.md) — Video calling, both robot to robot, and web to robot. Written in C++.

The three main modes are: Local, Cloud, Realtime. These can be chosen on screen.
Local: Runs with no WiFi internet needed.
Cloud: OpenAI LLM.
Realtime: OpenAI audio.

## Talk

The voice interaction.

```bash
cd talk
./install.sh
./talk.py
```

Say "robot" or "deskman" to wake it. The `talk.py` script uses OpenAI when online with a key in `talk/openai.env`, otherwise it uses local Gemma. Run `./install_openai_key.sh` to install an API key.

See [talk/README.md](talk/README.md).

## Robot

The face and servo control.

```bash
cd robot
./install.sh
mkdir build && cd build && cmake ..
make
./robot
```

The `./robot` program starts the face, servos, camera tracking, and `talk/talk.py`.

See [robot/README.md](robot/README.md).

## Teleport

The video call program.

```bash
cd teleport
./install.sh
mkdir build && cd build && cmake ..
make
./teleport
```

See [teleport/README.md](teleport/README.md).

## Cloud

The recordings sync.

```bash
cd robot/cloud
./install.sh
./sync.py
```

The `sync.py` script uploads new videos from `robot/recordings/` to the Neon `recordings` bucket, then rewrites the `index.json` the recordings page reads. It reads the Neon S3 keys from `robot/cloud/.env.local`. Use `--dry-run` to see what would upload.

## Setup

Run the installer from the repo root to install robot, talk, and teleport, enable the robot and teleport services, and configure the machine so it boots straight to the face:

```bash
./install.sh
sudo reboot
```

## Website

The Deskman website is at [teleportconnect.com](https://teleportconnect.com/).

