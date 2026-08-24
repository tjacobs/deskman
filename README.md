# Deskman

A desk robot: a face on a screen, a neck that looks around, local voice, and video calls.

- `talk/` — voice interaction. Wake word, speech to text, a local LLM, and speech back.
- `robot/` — the face and STS3215 servos, plus camera face tracking. C++.
- `teleport/` — video calling. C++.
- `website/` — product site, deployed to GitHub Pages.

Voice stays in Python. The C++ robot owns the display and the servos. `talk.py` and Teleport look commands move the head over `/tmp/robot.socket`.

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

`./robot` starts the face and, by default, `talk/talk.py`. `--no-talk` is body only. See `robot/README.md`.

## Teleport

```bash
cd teleport
./install.sh
mkdir -p build && cd build && cmake .. && make
./teleport
```

See `teleport/README.md`.

## Website

Static site in `website/`. Push to `main` deploys GitHub Pages from that folder.

