# Speak

Offline text to speech generation, using the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model.

Four tools:

- `speak.py` — speak a fixed phrase once, with timing stats
- `say.py` — interactive keyboard control over SSH, with preset phrases, voice and speed control
- `listen.py` — live speech to text from the microphone, using [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- `talk.py` — wake word loop, listens for a command and speaks a reply

CUDA is used when available for speak and say. Pass `--cpu` to force CPU inference. Every script takes `--help`.

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

Single keypresses work without Enter. Preset phrases are in `say.py` as `PHRASES`, triggered by keys `1`–`9`.

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

Say the wake word `robot`, then a command, and it speaks a reply from the local text model. Say `robot, quit` or CTRL-C to stop. Needs the same install as `listen.py`, plus `./install.sh --text`. Starts `./text/server.sh` itself when the model is not already running.

```bash
./install.sh --listen --text
./talk.py
```

Say it all in one breath, `robot, what is the time`, and it answers straight away. Say just `robot` and it replies `Question for me?`, then waits for the command. Right after `Hi!`, and after each reply, you can keep talking for 20 seconds without saying `robot` again.

The microphone stays open the whole time. A background thread reads it into blocks, the silero voice activity detector finds where each utterance starts and ends, and only whole utterances go to whisper. Nothing is missed between turns, and silence is never transcribed. The mic is muted only while it speaks, so it does not hear itself.

Each command is sent to Gemma through `text/ask.py`, and the spoken reply is whatever the model returns. Tools cover look left or right via `~/robot/src/look.py`, time and date, math and day counts, volume, and voice. Say `quit` or `exit` as the command and it says `Goodbye!` and stops. If talk.py started the text server, it stops it on exit. Conversations are appended to `talks/YYYY-MM-DD.txt`, including tool calls and results.

When it nearly hears its name, a transcription with `rob` or `rub` in it but not `robot`, it plays the recording back and says what it heard, so you can tell why it did not wake. Near miss words are in `NEAR_WAKE_WORDS`.

Pass `--test` to run one exchange and exit. It skips the wake word, speaks `What is the time?` so it hears itself through the mic, then answers. When the mic cannot hear the speaker it falls back to the question text.

Two flags help check what the mic picked up, and combine with each other and with `--test`:

- `--replay` plays the recording back after each utterance, saved as `audio/heard.wav`
- `--repeat` says the transcribed words back after each utterance

By default it also plays back what was said to it, the utterance with the wake word in it and any command that follows, so you can hear what it caught. Pass `--no-replay-robot` to turn that off. All mute the mic while playing, so it does not hear itself.

## talk service

Install a systemd service that runs `talk_service.sh` on boot, which starts `~/robot/src/real.py` and `talk.py` with `--no-replay-robot`:

```bash
./install_talk_service.sh
./install_talk_service.sh --start
./install_talk_service.sh --uninstall
```

```bash
sudo service talk start
sudo service talk stop
sudo service talk status
journalctl -u talk -f
tail -f ~/speak/log.txt
```

Optional memory sampling every 5 minutes into `monitor.log`:

```bash
(crontab -l 2>/dev/null | grep -v monitor_memory.sh; echo "*/5 * * * * $HOME/speak/monitor_memory.sh") | crontab -
```

After an SSH outage, check that file and run:

```bash
tail -50 ~/speak/monitor.log
dmesg -T | rg -i 'oom|killed process'
journalctl -b -1 -k | rg -i 'oom|killed process'
last -x | head
```

## Testing

```bash
./test.py
./test.py --fresh
```

Runs `speak.py` and `say.py --test` online, with `--cpu`, and offline, then `talk.py --test`. Pass `--fresh` to clear `cache/` and `audio/` first. Requires internet when the model is not cached.

The talk step is skipped when faster-whisper or a microphone is missing.

## Tools

- `tools-audio.sh` — route audio to any USB soundcard, disable onboard HDMI audio, on a Raspberry Pi or a Jetson. Run `./tools-audio.sh` once, it asks for sudo and sets audio up again on its own when an adapter is replugged
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
