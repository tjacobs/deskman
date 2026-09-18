# Talk

Speech to text, LLM inference, and text to speech generation.

Runs locally, offline, no cloud/internet/wifi needed once downloaded. With no flags, `talk.py` uses OpenAI when the internet and `OPENAI_API_KEY` are present, otherwise the local Gemma server. `--cloud` and `--local` override. `--realtime` skips the local speech models entirely and streams audio both ways to OpenAI.

Conversational AI bot with tools like time, date, volume, and google calendar integration.

Scripts:

- `speak.py` — speak a phrase once, with timing stats
- `say.py` — speak phrases by pressing keys, with voice and speed control
- `listen.py` — live speech to text transcription from the microphone
- `talk.py` — listens for wake word "robot" and a command, feeds it into the LLM, and speaks a reply
- `realtime.py` — streams microphone audio to OpenAI and plays the spoken reply, no local models in the loop
- `utils.py` — shared audio, cache, device, and microphone helpers for those scripts

The Nvidia CUDA GPU is used when available. Pass `--cpu` to force CPU inference. Every script takes `--help`.

```bash
./speak.py [optional text to speak]
./say.py
./listen.py
./talk.py
./realtime.py
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

Uses the whisper `base` model with voice activity detection, on GPU when available. Records with `arecord` from the USB microphone on linux, preferring a mic-only card over a speaker card's fallback mic, skipping camera cards that advertise capture with no mic, and with sox from the default input device on mac.

## setkey.py

Saves an OpenAI API key so `talk.py` can use the cloud. Opens the key page in a browser on the Pi screen, then shows a paste box on top of it.

```bash
./setkey.py
./setkey.py --run
```

Log in, create a key, and copy it. The paste box is prefilled from the clipboard, so a copied key only needs OK. Clicking the browser raises it over the box, tap `OpenAI key` in the taskbar to get back. The on-screen keyboard appears by itself when the box takes focus.

The key is checked against `https://api.openai.com/v1/models` before saving, so a mistyped or revoked key is refused rather than failing later. A good key is written to `openai.env` as `OPENAI_API_KEY=`, readable only by the current user. `--run` starts `talk.py` afterwards.

Use `--terminal` over SSH, it skips the browser and paste box and prompts on the terminal. That also happens on its own when there is no display or no browser.

## talk.py

Say the wake word `robot`, then a command, and it speaks a reply from the local text model. Say `robot, quit` or CTRL-C to stop. Starts `./text/server.sh` itself when the model is not already running. Only one `talk.py` is allowed at a time; stop the other with `sudo service robot stop`.

```bash
./install.sh --listen --talk
./talk.py
./talk.py --cloud
```

With no flags, talk pings `1.1.1.1` and uses OpenAI when that works and a key is in `OPENAI_API_KEY` or `openai.env`. Otherwise it starts the local Gemma server. `--cloud` forces OpenAI, `--local` forces Gemma. Optional: `TALK_LLM=cloud`, `TALK_CLOUD_MODEL` or `--model` (default `gpt-4o-mini`), `TALK_CLOUD_BASE` (default `https://api.openai.com`). Whisper and Kokoro stay local.

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

- `--replay` plays the recording back after every utterance, including room speech never aimed at it
- `--repeat` says the transcribed words back after each utterance
- `--memory` prints available RAM while models load, also printed automatically when free RAM is critically low

A near miss is always played back, whether or not `--replay` is set, since hearing it is the whole point of reporting one.

Pass `--realtime` to answer with OpenAI speech to speech instead of the local text and speech models. See below.

## realtime.py

Streams microphone audio up to the [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) over a WebSocket and plays the spoken reply back as it arrives, so the model hears you directly. Nothing is transcribed, sent as text, and read back out, which is what makes it answer faster and keep tone, pauses, and accent.

```bash
./realtime.py
./realtime.py "what is the time"
./talk.py --realtime
```

On its own, `realtime.py` opens the microphone and talks until the room goes quiet for 20 seconds. Pass a question to send that first instead of waiting for speech. `--model` and `--voice` override `config.json` for one run.

Under `talk.py --realtime`, the wake word still runs locally on whisper, and only what follows is streamed, so the microphone is not on a paid connection all day. Each conversation opens a session and closes it after the room has been quiet, and turns are written to `talks/` the same as any other.

The local tools all still work, bridged into Realtime function calls, so the head, clock, memory, reminders, and volume behave as usual. The kokoro voice tools are held back, they pick a voice OpenAI is not speaking with, so `set_voice` and `list_voices` are answered here instead with the ten OpenAI voices.

OpenAI fixes a session's voice once that session has spoken, so a voice change cannot be sent to the open one. Changing voice closes the session and opens a new one on the chosen voice, replays the last ten turns into it so nothing is forgotten, and has the new voice say a line so you hear the change. The session asked to switch stays silent, since it could only confirm in the old voice. A pick holds for the rest of the run and is not written to `config.json`, so a restart goes back to the configured voice. `list_voices` also reports how each voice sounds, male, female, or neutral, so asking for a man or a woman lands on one.

Realtime needs OpenAI, so it forces the cloud backend and never starts the local Gemma server, and it cannot be combined with `--local`. It needs `websocket-client`, which `install.sh` installs. Kokoro is not loaded at startup since replies arrive as OpenAI audio, so the greeting prints instead of being spoken and kokoro waits until a reminder or a goodbye actually needs a local voice. That takes talk from usable in about fifteen seconds to under two.

Audio only travels at 24 kHz mono, the only rate the API takes, so microphone blocks are resampled up from 16 kHz on the way out. The microphone is muted while it speaks, so it does not hear itself, which also means you cannot interrupt it mid-reply.

Input audio is transcribed by a second, cheap model purely so the heard lines reach the console and `talks/`. The model itself never reads that text. `gpt-live-transcribe` runs by default and sends words while you are still talking, so the `Heard:` line fills in as you speak. The model's own words are not a second transcriber, they arrive as `response.output_audio_transcript.delta` from the realtime model while it speaks, so the `Reply:` line fills in the same way. Set `TRANSCRIBE_LIVE` to false in `realtime.py` for the older `gpt-4o-mini-transcribe` on input and to print each `Reply:` in one go after the turn, which is what `PRINT_REPLY_LIVE` follows. Dropping the `transcription` key from the session would still answer normally, it would just have no record of what you said.

## config.json

Realtime settings live in `config.json`, next to `talk.py`. Anything missing falls back to the constants at the top of `realtime.py`.

```json
{
  "local": true,
  "realtime": false,
  "realtime_model": "gpt-realtime-2.1-mini",
  "realtime_voice": "echo",
  "realtime_accent": "You are a British man from London. Speak with a natural British accent."
}
```

- `local` — force the local Gemma server, the same as passing `--local`. This is how the robot service stays offline, since it takes no arguments
- `realtime` — answer with speech to speech, the same as passing `--realtime`. This is how the robot service turns it on, since it takes no arguments
- `realtime_model` — the realtime model, the mini one is cheaper and quick enough to hold a conversation
- `realtime_voice` — the OpenAI voice, separate from the kokoro `VOICES` the local path uses
- `realtime_accent` — appended to the system prompt, the voices are American by default and this is what makes one sound British

## robot service

Install a systemd service that runs `../robot/robot_service.sh` on boot.

```bash
../robot/install_robot_service.sh
../robot/install_robot_service.sh --start
../robot/install_robot_service.sh --uninstall
```

```bash
sudo service robot start
sudo service robot stop
sudo service robot status
journalctl -u robot -f
tail -f log.txt
```

The robot binary starts `talk.py` itself with a fixed set of arguments, so flags cannot be passed through the service. Set `"realtime": true` in `config.json` and restart to run the service on speech to speech. Set `"local": true` to force Gemma even when realtime is also on, `--cloud` and `--realtime` on the command line still win.

## Testing

```bash
./test.py
./test.py --fresh
```

Runs `speak.py` and `say.py --test` online, with `--cpu`, and offline, then `talk.py --test`, then `text/tests.py` (starts the text server if needed). Asks for sudo early for firewall offline tests. Pass `--fresh` to clear `cache/` and `audio/` first. Requires internet when the model is not cached.

## Tools

- `tools/audio.sh` — route audio to any USB soundcard, disable onboard HDMI audio, on a Pi or a Jetson.
- `tools/offline.sh` — block internet for offline testing, run with `--fix` to restore
- `tools/power.sh` — set Jetson power mode. No args shows status
- `tools/memory.sh` — sample free RAM and size to `memory.log`. Pass `--cron` for an every-5-minutes crontab.
- `tools/auth_google.py` — Google Calendar browser OAuth into `accounts.json`
- `tools/auth_sonos.py` — Sonos LAN or cloud setup into `accounts.json`
- `tools/test_audio.py` — confirm the speaker and mic work, plays a chime, records you talking on a live level meter, plays it back

```bash
./tools/power.sh        # status
./tools/power.sh min    # 15W, coolest/quietest, clocks scale with load
./tools/power.sh mid    # 25W, balanced
./tools/power.sh max    # 25W uncapped, full performance, clocks locked high
./tools/memory.sh       # append one memory sample to tools/memory.log
./tools/memory.sh --cron
./tools/auth_google.py
./tools/auth_sonos.py
./tools/test_audio.py
./tools/test_audio.py --meter
```

`test_audio.py` counts down, then records one clip behind a live level meter. The chime plays into the opening two seconds of that clip, and you talk for the remaining four. It reports the cards in use and checks six things: the chime played, the recording is a valid wav, the mic heard the chime, the mic heard your voice, the recording looks like speech, and the recording played back. It exits non-zero when any of those fail, and keeps the recording at `audio/test_capture.wav`.

A passing check prints only its green `PASS` line. Measurements are printed just for the checks that fail, along with what to look at, so a good run stays short and a bad one says why.

Recording the chime rather than just playing it makes the speaker and mic prove themselves together, with no one in the room. The notes are looked for in the opening window, so a speaker that is muted or a mic that hears nothing both fail on their own line. The chime window and the talking window are analysed separately, otherwise the chime would supply the level swing and speech band energy that the speech check is there to find, and a silent room would pass.

Unlike the other scripts here it runs from any directory, including `tools/` itself. Its shebang is `#!/usr/bin/env python3` and it hands over to `.venv/bin/python` on startup, rather than relying on the relative `#!.venv/bin/python` that only resolves from `talk/`.

The meter is an ASCII bar per channel in dBFS, redrawn in place, with `|` marking the loudest level so far. `--meter` shows it on its own until CTRL-C, which is the quickest way to see whether the mic responds at all.

```
mic  -30.0 dBFS [####################------------|-------]
L  -30.0 dBFS [####################------------|-------]  R  -18.0 dBFS [############################--------|---]
```

Channel count comes from the capture section of `/proc/asound/card<n>/stream0`, so a mono mic gets one bar and a stereo card gets separate left and right bars. Asking `plughw` for two channels on a mono mic succeeds but only duplicates the one channel, so the meter follows the hardware rather than the request. One recorder streams raw frames that feed both the meter and the saved wav, so what you watch is exactly what gets checked.

The speech check exists because a mic jack with nothing in it still hisses loud enough to pass a plain volume check. Real speech swings in level between words and puts most of its energy under 4 kHz, so a flat recording full of high frequency hiss is reported as a dead input rather than a working mic.

