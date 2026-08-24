# Deskman

A desk robot: a face on a screen that looks around, wth local voice assistant, and video calls.

- `talk/` — voice interaction. Wake word, speech to text, a local LLM, and speech back. Python.
- `robot/` — The face and servo control, and camera face tracking. C++.
- `teleport/` — Video calling, both robot to robot, and web to robot. C++.
- `website/` — Product site, deployed to GitHub Pages.

## Talk

```bash
cd talk
./install.sh --listen --talk
./talk.py
```

See `talk/README.md`.

## Robot

```bash
cd robot
mkdir -p build && cd build && cmake .. && make
./robot
```

`./robot` starts the face and `talk/talk.py`.

See `robot/README.md`.

## Teleport

```bash
cd teleport
./install.sh
mkdir -p build && cd build && cmake .. && make
./teleport
```

See `teleport/README.md`.

## Website

Static site in `website/`. Push to `main` deploys GitHub Pages.

