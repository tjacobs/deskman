#!/usr/bin/env bash

# Stop on errors and unset variables
set -euo pipefail

# Paths
TEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="${TEXT_DIR}/llama.cpp/build/bin/llama-server"
MODEL="${TEXT_DIR}/models/gemma-4-E2B-it-Q4_K_S.gguf"
LIBRARY_DIR="${TEXT_DIR}/llama.cpp/build/bin"
CUDA_LIBRARY="${LIBRARY_DIR}/libggml-cuda.so"
CACHE_PATH="${TEXT_DIR}/cache"

# Server config
MODEL_ALIAS="gemma-4-e2b"
HOST="127.0.0.1"
PORT="8080"
API_KEY="local"
CONTEXT_SIZE="4096"
GPU_LAYERS="all"
PARALLEL_REQUESTS="1"

# Generating is memory bound so extra threads only add contention, reading the prompt is compute bound so it wants every core
THREADS="2"
THREADS_BATCH="4"

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

    # Show server address and whether this build can use a GPU
    echo "Model: ${MODEL_ALIAS}"
    echo "API: http://${HOST}:${PORT}/v1"
    server_args=(--model "${MODEL}" --alias "${MODEL_ALIAS}" --host "${HOST}" --port "${PORT}" --api-key "${API_KEY}" --ctx-size "${CONTEXT_SIZE}" --threads "${THREADS}" --threads-batch "${THREADS_BATCH}" --parallel "${PARALLEL_REQUESTS}" --slot-save-path "${CACHE_PATH}" --reasoning off --reasoning-format none --verbosity 1)
    if [[ -e "${CUDA_LIBRARY}" ]]; then
        echo "Device: GPU"
        server_args+=(--gpu-layers "${GPU_LAYERS}")
    else
        echo "Device: CPU"
    fi

    # Keep a cache dir so ask.py --clear can erase the prompt cache
    mkdir -p "${CACHE_PATH}"

    # Load llama.cpp libraries from the current text directory
    export LD_LIBRARY_PATH="${LIBRARY_DIR}:${LD_LIBRARY_PATH:-}"

    # Run local OpenAI compatible server, --slot-save-path is llama.cpp's name for the cache dir
    exec "${SERVER}" "${server_args[@]}"
}

# Run
main "$@"
