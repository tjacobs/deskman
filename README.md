# Speak

Speech to text, LLM inference, and text to speech generation.

Runs locally, offline, no cloud/internet/wifi needed once downloaded.

Conversational AI bot with tools like time, date, volume, and google calendar integration.

Four scripts:

- `speak.py` — speak a phrase once, with timing stats
- `say.py` — speak phrases by pressing keys, with voice and speed control
- `listen.py` — live speech to text transcription from the microphone
- `talk.py` — listens for wake word "robot" and a command, feeds it into the LLM, and speaks a reply

The Nvidia CUDA GPU is used when available. Pass `--cpu` to force CPU inference. Every script takes `--help`.

```bash
./speak.py [optional text to speak]
./say.py
./listen.py
./talk.py
./test.py
```

- Speech to text: [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- Local LLM: [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B)
- Text to speech: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)

## Setup

```bash
./install.sh --listen --talk
```

Works on linux and mac. Installs [uv](https://docs.astral.sh/uv/) when missing, installs system requirements, then creates `.venv` and installs kokoro and torch into it.

Pass `--listen --talk` to also install speech to text for `listen.py` and `talk.py`.

On first run, the model and all voices download into `cache/`.

## speak.py

Speak a single block of text. Pass optional words to speak them instead of the `TEXT` constant in `speak.py`.

Generated audio files are saved in `audio/` as `001.wav`, `002.wav`, etc.

## say.py

Interactive speech tool. Press keys to speak phrases.

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

Live transcription from the microphone. Speak into your microphone, and text lines will print as you talk, CTRL-C to stop.

```bash
./install.sh --listen
./listen.py
```

On a machine with the CUDA toolkit, `--listen` clones and builds [CTranslate2](https://github.com/OpenNMT/CTranslate2) with CUDA for the Jetson GPU, then installs it and faster-whisper into `.venv`. Everywhere else it installs the CPU wheels from PyPI, plus sox on mac.

Uses the whisper `base` model with voice activity detection, on GPU when available. Records with `arecord` from the USB microphone on linux, preferring a mic-only card over a speaker card's fallback mic, and with sox from the default input device on mac.

## talk.py

Say the wake word `robot`, then a command, and it speaks a reply from the local text model. Say `robot, quit` or CTRL-C to stop. Starts `./text/server.sh` itself when the model is not already running. Only one `talk.py` is allowed at a time; stop the other with `sudo service robot stop` or `sudo service talk stop`.

```bash
./install.sh --listen --talk
./talk.py
```

Say `robot what is the time`, and it answers straight away. Say just `robot` and it replies `Question for me?`, then waits for the command. You can keep talking for 20 seconds after a reply without saying `robot` again.

The microphone stays open the whole time. A background thread reads it into blocks, the Silero voice activity detector finds where each utterance starts and ends, and only whole utterances go to whisper. The mic is muted while it speaks, so it does not hear itself.

Each command is sent to Gemma through `text/ask.py`, and the spoken reply is whatever the model returns. Tools are: time and date, math and days, volume, voice, long-term remember/forget, and daily reminders.

Facts you ask it to remember are saved in `memory.json` and survive reboot. Daily reminders are saved in `reminders.json`; while talk is running, a background check speaks each one once per day at its clock time. 

Say `quit` or `exit` as the command and it says `Goodbye!` and stops. If talk.py started the text server, it stops it on exit. 

Conversations are appended to `talks/YYYY-MM-DD.txt`, including tool calls and results. You can ask it to remember a day's conversations to put it into memory context.

Say `remind me at dinner time` or `remind me at 10 PM for bedtime` to schedule a spoken reminder. Say `what reminders do I have` to list them, or `cancel the dinner reminder` to remove one. On startup, dinner and bedtime reminders are seeded from matching memory facts when those reminders are missing.

When it nearly hears its name, a transcription with `rob` or `rub` in it but not `robot`, it plays the recording back and says what it heard, so you can tell why it did not wake. Near miss words are in `NEAR_WAKE_WORDS`.

On an 8 GB machine the text server, whisper, and kokoro together need a few GB of RAM. Talk loads whisper and kokoro first, then the text server, so a tight memory load does not kill an already-running Gemma. It warns when free RAM is below the expected cost before loading. If the text server dies later, often from out of memory, talk prints that and tries to restart it a few times before giving up. Startup failures are logged to `text_server.log`.

Pass `--test` to run one exchange and exit. It skips the wake word, speaks `What is the time?` so it hears itself through the mic, then answers. When the mic cannot hear the speaker it falls back to the question text.

Flags that help debug audio and memory, and combine with each other and with `--test`:

- `--replay` plays the recording back after each utterance, saved as `audio/heard.wav`
- `--repeat` says the transcribed words back after each utterance
- `--memory` prints available RAM while models load, also printed automatically when free RAM is critically low

By default it also plays back what was said to it, so you can hear what it heard. Pass `--no-replay-robot` to turn that off.

## talk service

Install a systemd service that runs `talk_service.sh` on boot, which starts `talk.py` with `--no-replay-robot`:

```bash
./talk_service_install.sh
./talk_service_install.sh --start
./talk_service_install.sh --uninstall
```

```bash
sudo service talk start
sudo service talk stop
sudo service talk status
journalctl -u talk -f
tail -f log.txt
```

## Testing

```bash
./test.py
./test.py --fresh
```

Runs `speak.py` and `say.py --test` online, with `--cpu`, and offline, then `talk.py --test`. Pass `--fresh` to clear `cache/` and `audio/` first. Requires internet when the model is not cached.

## Tools

- `tools/audio.sh` — route audio to any USB soundcard, disable onboard HDMI audio, on a Raspberry Pi or a Jetson.
- `tools/offline.sh` — block internet for offline testing, run with `--fix` to restore
- `tools/power.sh` — set Jetson power mode. No args shows status
- `tools/memory.sh` — sample free RAM and talk process size into `memory.log`. Pass `--cron` for an every-5-minutes crontab.

```bash
./tools/power.sh        # status
./tools/power.sh min    # 15W, coolest/quietest, clocks scale with load
./tools/power.sh mid    # 25W, balanced
./tools/power.sh max    # 25W uncapped, full performance, clocks locked high
./tools/memory.sh       # append one memory sample to tools/memory.log
./tools/memory.sh --cron
```

