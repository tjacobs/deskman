# Local LLM

Runs Gemma 4 E2B locally through the OpenAI-compatible `llama.cpp` server.

## Run

Start the server:

```bash
cd ~/speak/text
./server.sh
```

In another terminal, ask a question:

```bash
cd ~/speak/text
./ask.py "What time is it?"
```

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

## Files

- `server.sh` starts Gemma 4 E2B with GPU acceleration when available and a 4096-token context.
- `ask.py` sends one request and runs the tools the model calls.
- `move.py` owns the look tool through `~/robot/src/look.py`.
- `dates.py` owns clock, calendar, and day-count helpers.
- `maths.py` owns the calculate tool and safe expression evaluation.
- `memory.py` owns long-term remember/forget tools and storage.
- `reminders.py` owns daily reminder tools, storage, and due checks.
- `voice.py` owns speaking voice tools through talk.py.
- `volume.py` owns speaker volume tools and amixer control.
- `../prompt.json` holds the system prompt read on each ask.
- `../memory.json` holds long-term facts.
- `../reminders.json` holds daily spoken reminders.
- `llama.cpp/` builds the server with CUDA when available, else CPU.
- `models/` contains GGUF model weights.
- Prior questions, tool calls, tool results, and answers stay in process memory for later asks in the same session.
