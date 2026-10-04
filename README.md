# Deskman

Deskman is a desktop robot: a face on a screen that looks around, with an interactive voice assistant, that can make video calls. It runs on a Jetson Orin Nano or a Raspberry Pi 5. The main parts are:

- `[talk](talk/README.md)` — Voice interaction. Wake word, speech to text, an LLM, and speech generation. Runs locally on Gemma, or on OpenAI when online. Written in Python.
- `[robot](robot/README.md)` — The face, servo control, camera face tracking, and the on-screen menu. Written in C++.
- `[teleport](teleport/README.md)` — Video calling, both robot to robot, and web to robot. Written in C++.

## Talk

The voice interaction.

```bash
cd talk
./install.sh --listen --talk
./talk.py
```

Say "robot" or "deskman" to wake it. The `talk.py` script uses OpenAI when online with a key in `talk/openai.env`, otherwise it uses local Gemma. Run `./install_openai_key.sh` to install an API key.

See [talk/README.md](talk/README.md).

## Robot

The face and servo control.

```bash
cd robot
./install.sh
mkdir build && cd build && cmake .. && make
./robot
```

The `./robot` program starts the face, servos, camera tracking, and `talk/talk.py`.

See [robot/README.md](robot/README.md).

## Teleport

The video call program.

```bash
cd teleport
./install.sh
mkdir build && cd build && cmake .. && make
./teleport
```

See [teleport/README.md](teleport/README.md).

## Setup

Install talk, robot, and teleport, then run this to configure the machine so it boots straight to the face:

```bash
cd robot
./install_system.sh
./install_robot_service.sh
cd ../teleport
./install_teleport_service.sh
sudo reboot
```

