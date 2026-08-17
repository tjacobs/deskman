# Local LLM

Runs a local Gemma model through the OpenAI-compatible `llama.cpp` server. Default is Gemma 4 E2B so tool calling works; use `1b` for speed when you do not need tools.

## Run

Start the server:

```bash
cd ~/speak/text
./server.sh        # Gemma 4 E2B, default, tool calling works
./server.sh e2b    # same as default
./server.sh 1b     # Gemma 3 1B, faster, but no native tool calling
```

In another terminal, ask a question:

```bash
cd ~/speak/text
./ask.py "What time is it?"
./ask.py --test
./tests.py
```

`./ask.py --test` and `./tests.py` run the tool suite in `tests.py`, one ask per tool. They start `./server.sh` when the text server is not already up, and stop it again if they started it.

The API is available only on the local machine:

```text
http://127.0.0.1:8080/v1/chat/completions
```

## Tools

### Movement

When you ask it to look left or right, it calls `~/robot/src/look.py` through an LLM tool, default 60 degrees, max 90.

### Time

When you ask the time, today's date, or day, Python answers through `get_time`, `get_date`, and `get_day` tools.

### Math

Math and day counts go through the `calculate` tool, which evaluates a Python expression, including date math like `(date(2026, 8, 15) - today()).days`.

### Dates

Day counts like "how many days until August 15" are worked out in Python rather than left to the model, which guesses at arithmetic. The helpers in `dates.py` turn the question into a `date(...)` subtraction and assume the next matching future date when you leave the year out.

### Volume

Volume uses `set_volume` and `get_volume` through amixer.

### Voice

Voice uses `set_voice` and `list_voices` with the Kokoro voices from `say.py`.

### Memory

Facts you ask it to remember use `remember` and `forget`, saved in `../memory.json` across reboots.

### Reminders

Daily spoken reminders use `set_reminder`, `cancel_reminder`, and `list_reminders`, saved in `../reminders.json`.

### Talk logs

`load_talk_log` reads `../talks/YYYY-MM-DD.txt` into context for today, yesterday, or a date, so the model can answer questions about prior conversations.

### System

`get_system_info` reports `uname`, hardware like Jetson or Pi, memory, and live LLM model stats from the local server.

## Files

- `server.sh [e2b|1b]` starts the local model with a 4096-token context by default. Set `CONTEXT_SIZE` to override. If `cache/grow_to` is written, it restarts with that larger context.
- `ask.py` sends one request and runs the tools the model calls.
- `tests.py` runs one ask per tool and checks each expected tool ran.
- `client.py` is the LLM server interface and shared API config. It also grows context on overflow and retries.
- `move.py` owns the look tool through `~/robot/src/look.py`.
- `dates.py` owns clock, calendar, and day-count helpers.
- `maths.py` owns the calculate tool and safe expression evaluation.
- `memory.py` owns long-term remember/forget tools and storage.
- `reminders.py` owns daily reminder tools, storage, and due checks.
- `talks.py` owns loading a day's talk log into context.
- `system.py` owns computer and LLM model info.
- `voice.py` owns speaking voice tools through talk.py.
- `volume.py` owns speaker volume tools and amixer control.
- `accounts/` owns Google Calendar and Sonos tools, plus `accounts.json` setup CLIs.
- `../tools/auth_google.py` one-line Google Calendar browser setup.
- `../tools/auth_sonos.py` one-line Sonos LAN or cloud setup guide.
- `../text_prompt.json` holds the system prompt read on each ask.
- `../memory.json` holds long-term facts.
- `../reminders.json` holds daily spoken reminders.
- `../accounts.json` holds third-party account tokens, gitignored. Create it with `../tools/auth_google.py` or `../tools/auth_sonos.py`.
- `llama.cpp/` builds the server with CUDA when available, else CPU.
- `models/` contains GGUF model weights.
- Prior questions, tool calls, tool results, and answers stay in process memory for later asks in the same session.
