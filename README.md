# Deskman

A desk robot: a face on a screen that looks around, wth local voice assistant, and video calls.

- [`talk/`](talk/README.md) — Voice interaction. Wake word, speech to text, a local LLM, and speech back. Python.
- [`robot/`](robot/README.md) — The face and servo control, and camera face tracking. C++.
- [`teleport/`](teleport/README.md) — Video calling, both robot to robot, and web to robot. C++.
- [`website/`](website/) — Product site, deployed to GitHub Pages.

## Talk

```bash
cd talk
./install.sh --listen --talk
./talk.py
```

See [talk/README.md](talk/README.md).

## Robot

```bash
cd robot
mkdir -p build && cd build && cmake .. && make
./robot
```

`./robot` starts the face and `talk/talk.py`.

See [robot/README.md](robot/README.md).

## Teleport

```bash
cd teleport
./install.sh
mkdir -p build && cd build && cmake .. && make
./teleport
```

See [teleport/README.md](teleport/README.md).

## Website

Static site in [`website/`](website/). Push to `main` deploys GitHub Pages.

