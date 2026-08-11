Style guide

Functions:

- Main is the first function in the file
- Parse args is the second function in the file
- Functions go in the file in the order they are called, so reads like a book, with utils at end
- Keep main minimal, have it just call functions mainly

Comments:

- Comments are short descriptions of what the code block does, like "# Create policy", not passive "# Policy"
- Always insert a blank line immediately before every comment block, except for at top of functions
- Every block of code needs a comment above it, with a blank line above the comment
- No line of code should exist just by itself, put a comment above it or group it
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

## Cursor Cloud specific instructions

This is an offline text to speech project. Setup is `./install.sh`, which installs `uv` into `~/.local/bin`, apt packages `espeak-ng` and `alsa-utils`, creates `.venv` on Python 3.12, and installs kokoro, torch (CPU here, no GPU), soundfile, soco. See `README.md` and `install.sh` for the full command set.

Run everything from the repo root. The script shebangs are relative, `#!.venv/bin/python`, so `./speak.py` only resolves from `/workspace`. Otherwise use `./.venv/bin/python speak.py`.

The cloud VM has no audio hardware, no ALSA card in `/proc/asound`, no microphone, and no GPU. Kernel modules and `/dev/snd` are absent and `/proc/asound` cannot be created, so no real or dummy ALSA card can be loaded.

- `speak.py` and `say.py` detect the missing soundcard, print `Audio playback unavailable: ..., generating without playback.`, and keep running, so they still write WAVs to `audio/` and `./test.py` passes here. Only speaker playback is skipped, generation is unaffected.

The Kokoro model and voices download into `cache/` on first run and need internet. Once cached, `HF_HUB_OFFLINE=1` works offline.

`listen.py` and `talk.py` need a USB microphone that this VM does not have. `talk.py` also needs `./install.sh --listen` and `./install.sh --talk`, where `--talk` builds `llama.cpp` and downloads a ~3GB Gemma GGUF, and it starts `llama-server` on port 8080. `text/tests.py` needs that server running. Stop `llama-server` when done, see `.cursor/rules/stop-llm-server.mdc`.

