#!/usr/bin/env bash

# Stop on errors and unset variables
set -euo pipefail

# Paths
TEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="${TEXT_DIR}/llama.cpp/build/bin/llama-server"
LIBRARY_DIR="${TEXT_DIR}/llama.cpp/build/bin"
CUDA_LIBRARY="${LIBRARY_DIR}/libggml-cuda.so"
CACHE_PATH="${TEXT_DIR}/cache"
MODELS_DIR="${TEXT_DIR}/models"

# Default model preset, override with ./server.sh 1b or ./server.sh e2b
DEFAULT_PRESET="e2b"

# Server config
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
    # Pick model preset from the first arg
    local preset="${1:-${DEFAULT_PRESET}}"
    if [[ "${preset}" == "-h" || "${preset}" == "--help" ]]; then
        print_usage
        exit 0
    fi
    select_model "${preset}"

    # Check server binary
    if [[ ! -x "${SERVER}" ]]; then
        echo "llama-server not built: ${SERVER}"
        exit 1
    fi

    # Download the selected model when missing
    ensure_model

    # Show server address and whether this build can use a GPU
    echo "Model: ${MODEL_ALIAS}"
    echo "File: ${MODEL_PATH}"
    echo "API: http://${HOST}:${PORT}/v1"
    server_args=(--model "${MODEL_PATH}" --alias "${MODEL_ALIAS}" --host "${HOST}" --port "${PORT}" --api-key "${API_KEY}" --ctx-size "${CONTEXT_SIZE}" --threads "${THREADS}" --threads-batch "${THREADS_BATCH}" --parallel "${PARALLEL_REQUESTS}" --slot-save-path "${CACHE_PATH}" --reasoning off --reasoning-format none --verbosity 1)
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

# Print usage help
print_usage() {
    echo "Usage: ./server.sh [e2b|1b]"
    echo "  e2b  Gemma 4 E2B Q4_K_S, default, better quality"
    echo "  1b   Gemma 3 1B Q4_K_M, smaller and faster on Pi"
}

# Set MODEL_PATH, MODEL_ALIAS, and MODEL_URL for a preset name
select_model() {
    local preset="$1"
    case "${preset}" in
        e2b)
            MODEL_ALIAS="gemma-4-e2b"
            MODEL_NAME="gemma-4-E2B-it-Q4_K_S.gguf"
            MODEL_URL="https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/${MODEL_NAME}"
            ;;
        1b|small)
            MODEL_ALIAS="gemma-3-1b"
            MODEL_NAME="gemma-3-1b-it-Q4_K_M.gguf"
            MODEL_URL="https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/${MODEL_NAME}"
            ;;
        *)
            echo "Unknown model preset: ${preset}"
            print_usage
            exit 1
            ;;
    esac
    MODEL_PATH="${MODELS_DIR}/${MODEL_NAME}"
    MODEL_PART="${MODEL_PATH}.part"
}

# Download the selected model when it is not on disk yet
ensure_model() {
    if [[ -f "${MODEL_PATH}" ]]; then
        return 0
    fi
    mkdir -p "${MODELS_DIR}"
    echo "Downloading ${MODEL_NAME}..."
    curl --fail --location --continue-at - --output "${MODEL_PART}" "${MODEL_URL}"
    mv "${MODEL_PART}" "${MODEL_PATH}"
}

# Run
main "$@"
