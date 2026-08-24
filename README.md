# Deskman

A desk robot: a face on a screen that looks around, with local voice, and video calls.

- `talk/` — Voice interaction. Wake word, speech to text, a local LLM, and speech back. Python.
- `robot/` — The face and STS3215 servos, plus camera face tracking. C++.
- `teleport/` — Video calling, robot to robot and web to robot.


## Talk

```bash
cd talk
./install.sh --listen --talk
./talk.py
```

`./talk.py` starts the interactive voice assistant.

See `talk/README.md`.

## Robot

```bash
cd robot
mkdir -p build && cd build && cmake .. && make
./robot
```

`./robot` starts the face and starts `talk/talk.py`.

See `robot/README.md`.
