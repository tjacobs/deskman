Style guide

Functions:

- Main is the first function in the file
- Parse args is the second function in the file
- Functions go in the file in the order they are called, so reads like a book
- Keep main minimal, have it just call functions mainly

Comments:

- Every block of code needs a comment above it, with a blank line above the comment
- No line of code should exist just by itself, put a comment above it or group it
- Comments are short descriptions of what code blocks do, like "# Create the thing", not a passive "# Thing"
- Always insert a blank line immediately before every comment block, except for at top of functions
- No need for blank lines at the start of functions above the first comment
- No doc strings, just put a single line comment above each function
- No parens in comments, use commas instead
- No comments at end of lines

Code:

- Allow running the program with no args to do something useful, so have defaults
- Fix or supress any warnings that occur in output, working run should be clean
- Try to not have value defaults in function define lines
- Function calls should be on one line, not broken over many lines
- Remove any imports not used

Naming:

- No variable or function name short abbreviations (e.g. ok to use config, but not cfg)
- Keep code simple and minimal number of lines
- No underscores in front of functions
- Use full variable and function names
- Prefer arg names load and save



## Cursor instructions

Talk lives in `talk/`. Robot C++ lives in `robot/`, face, servos, and camera only. Voice is Python.

Talk setup is `cd talk && ./install.sh`, which installs `uv` into `~/.local/bin`, apt packages `espeak-ng` and `alsa-utils`, creates `.venv` on Python 3.12, and installs kokoro, torch, soundfile, soco. See `talk/README.md` and `talk/install.sh`.

Robot build: `cd robot && mkdir -p build && cd build && cmake .. && make`, then `./robot`. `--no-talk` skips spawning `talk/talk.py`. `--camera` shows the tracking preview.

Teleport build: `cd teleport && mkdir -p build && cd build && cmake .. && make`, then `./teleport`.

Run talk scripts from `talk/`. Shebangs are relative, `#!.venv/bin/python`, so `./speak.py` only resolves from that directory. Otherwise use `talk/.venv/bin/python talk/speak.py`.

The cloud VM has no audio hardware, no ALSA card in `/proc/asound`, no microphone, and no GPU. Kernel modules and `/dev/snd` are absent and `/proc/asound` cannot be created, so no real or dummy ALSA card can be loaded.

- `talk/speak.py` and `talk/say.py` detect the missing soundcard, print `Audio playback unavailable: ..., generating without playback.`, and keep running, so they still write WAVs to `talk/audio/` and `talk/test.py` passes here. Only speaker playback is skipped, generation is unaffected.

The Kokoro model and voices download into `talk/cache/` on first run and need internet. Once cached, `HF_HUB_OFFLINE=1` works offline.

`talk/listen.py` and `talk/talk.py` need a USB microphone that this VM does not have. `talk.py` also needs `./install.sh --listen` and `./install.sh --talk` from `talk/`, where `--talk` builds `llama.cpp` and downloads a ~3GB Gemma GGUF, and it starts `llama-server` on port 8080. `talk/text/tests.py` needs that server running. Stop `llama-server` when done.

When you start or use `talk/text/server.sh` / `llama-server` for testing, benches, or debugging:

- Stop it before ending your turn once you no longer need it
- Prefer killing the `llama-server` process, or Ctrl+C equivalent, so port 8080 is free
- Do not leave a background model server running after the task is finished
- Exception: only leave it running if the user explicitly asks to keep the server up



