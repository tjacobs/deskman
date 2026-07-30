#!/usr/bin/env bash

# Stop on errors and unset variables
set -euo pipefail

# Paths
TEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="${TEXT_DIR}/llama.cpp/build/bin/llama-server"
MODEL="${TEXT_DIR}/models/gemma-4-E2B-it-Q4_K_S.gguf"
LIBRARY_DIR="${TEXT_DIR}/llama.cpp/build/bin"

# Server config
MODEL_ALIAS="gemma-4-e2b"
HOST="127.0.0.1"
PORT="8080"
API_KEY="local"
CONTEXT_SIZE="4096"
THREADS="6"
GPU_LAYERS="all"
PARALLEL_REQUESTS="1"

# Main
main() {
    # Check server binary
    if [[ ! -x "${SERVER}" ]]; then
        echo "llama-server not built: ${SERVER}"
        exit 1
    fi

    # Check model
    if [[ ! -f "${MODEL}" ]]; then
        echo "Model not found: ${MODEL}"
        exit 1
    fi

    # Show server address
    echo "Model: ${MODEL_ALIAS}"
    echo "API: http://${HOST}:${PORT}/v1"

    # Load llama.cpp libraries from the current text directory
    export LD_LIBRARY_PATH="${LIBRARY_DIR}:${LD_LIBRARY_PATH:-}"

    # Run local OpenAI compatible server
    exec "${SERVER}" --model "${MODEL}" --alias "${MODEL_ALIAS}" --host "${HOST}" --port "${PORT}" --api-key "${API_KEY}" --ctx-size "${CONTEXT_SIZE}" --threads "${THREADS}" --gpu-layers "${GPU_LAYERS}" --parallel "${PARALLEL_REQUESTS}" --reasoning off --reasoning-format none --verbosity 1
}

# Run
main "$@"
