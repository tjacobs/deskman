# Speak

Offline text to speech generation, using the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model.

Four tools:

- `speak.py` — speak a fixed phrase once, with timing stats
- `say.py` — interactive keyboard control over SSH, with preset phrases, voice and speed control
- `listen.py` — live speech to text from the microphone, using [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- `talk.py` — wake word loop, listens for a command and speaks a reply

Both use CUDA when available. Pass `--cpu` to force CPU inference.

```bash
./speak.py
./speak.py --cpu
./say.py
./say.py --cpu
```

## Setup

```bash
./install.sh
./install.sh --listen
```

Works on linux and mac. Installs [uv](https://docs.astral.sh/uv/) when missing, installs espeak-ng with brew or apt, plus alsa-utils on linux, then creates `.venv` and installs kokoro, torch, and soundfile into it.

Pass `--listen` to also install speech to text for `listen.py` and `talk.py`, see below.

On first run, the model and all voices download into `cache/`.

## speak.py

Speak a single block of text. Edit the `TEXT` constant in `speak.py` to change what is spoken.

Generated audio files are saved in `audio/` as `001.wav`, `002.wav`, etc.

## say.py

Interactive speech tool. Press keys to speak phrases.

Single keypresses work without Enter. Preset phrases are in `phrases.json`, triggered by keys `1`–`9`.

### Controls

| Key | Action |
|-----|--------|
| `t` | Type a custom phrase |
| `r` | Repeat last custom phrase |
| `c` | Cancel current speech |
| `x` | Clear queued speech |
| `+` / `-` | Speed up / down |
| `v` | Next voice |
| `h` | Show help |
| `q` | Quit |

Default speed is 1.5x. Use `+` / `-` to adjust, `v` to change voice.

Generated audio files are saved in `audio/`.

Pass `--test` to speak the first two preset phrases and exit.

## listen.py

Live transcription from the microphone. Speak and lines print as you talk, CTRL-C to stop.

```bash
./install.sh --listen
./listen.py
```

On a machine with the CUDA toolkit, `--listen` clones and builds [CTranslate2](https://github.com/OpenNMT/CTranslate2) with CUDA for the Jetson GPU, then installs it and faster-whisper into `.venv`. The build takes around 30 minutes and only runs once. Everywhere else it installs the CPU wheels from PyPI, plus sox on mac.

Uses the whisper `base` model with voice activity detection, on GPU when available. Records with `arecord` from the first USB soundcard with a mic on linux, and with sox from the default input device on mac.

## talk.py

Say the wake word `robot`, then a command, and it speaks a reply. CTRL-C to stop. Needs the same install as `listen.py`.

```bash
./install.sh --listen
./talk.py
```

Replies to a few built in commands like the time, the date, and its name, and echoes anything else back. Edit `make_reply` in `talk.py` to add more.

Pass `--test` to run one exchange and exit. It skips the wake word, speaks `What is the time?` so it hears itself through the mic, then answers. When the mic cannot hear the speaker it falls back to the question text.

## Testing

```bash
./test.py
./test.py --fresh
```

Runs `speak.py` and `say.py --test` online, with `--cpu`, and offline, then `talk.py --test`. Pass `--fresh` to clear `cache/` and `audio/` first. Requires internet when the model is not cached.

The talk step is skipped when faster-whisper or a microphone is missing.

## Tools

- `tools-audio.sh` — route audio to any USB soundcard, disable HDMI and APE outputs. Run `./tools-audio.sh --install` once, then `./tools-audio.sh` after plugging in a new adapter
- `tools-power.sh` — set Jetson power mode. No args shows status

```bash
./tools-power.sh        # status
./tools-power.sh min    # 15W, coolest/quietest, clocks scale with load
./tools-power.sh mid    # 25W, balanced
./tools-power.sh max    # 25W uncapped, full performance, clocks locked high
```

- `tools-offline.sh` — block internet for offline testing, `./tools-offline.sh --fix` to restore

## Notes

- First `say.py` launch downloads all voices and takes longer. Later launches are faster.
- `min` / `mid` turn off `jetson_clocks` so CPU frequency can drop when idle. `max` turns it back on. `mid` and `max` are both 25W-class; `max` unlocks clocks fully (MAXN_SUPER).
