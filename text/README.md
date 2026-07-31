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

Use `Authorization: Bearer local` for direct API requests. `ask.py` includes this automatically.

When you ask it to look left or right, it calls `~/robot/src/look.py` through an LLM tool, default 60 degrees, max 90. When you ask the time, today's date, or day, Python answers through `get_time`, `get_date`, and `get_day` tools. Math and day counts go through the `calculate` tool, which evaluates a Python expression, including date math like `(date(2026, 8, 15) - today()).days`. Volume uses `set_volume` and `get_volume` through amixer. Voice uses `set_voice` and `list_voices` with the Kokoro voices from `say.py`.

## Files

- `server.sh` starts Gemma 4 E2B with GPU acceleration when available and a 4096-token context.
- `ask.py` sends a short voice-assistant request and can call look, time, date, and day tools.
- `../prompt.json` holds the system prompt read on each ask.
- Prior questions, tool calls, tool results, and answers stay in memory for later asks in the same process.
- `llama.cpp/` contains the native CUDA runtime.
- `models/` contains GGUF model weights.
