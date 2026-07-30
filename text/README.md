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

## Files

- `server.sh` starts Gemma 4 E2B with GPU acceleration when available and a 4096-token context.
- `ask.py` sends a short voice-assistant request.
- `llama.cpp/` contains the native CUDA runtime.
- `models/` contains GGUF model weights.
