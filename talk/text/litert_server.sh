#!/usr/bin/env bash

# Serve Gemma through LiteRT-LM with a cached prompt on the llama-server port, talk.py starts this when litert is true in config.json

# Stop on errors and unset variables
set -euo pipefail

# Paths
TEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LITERT_DIR="${TEXT_DIR}/litert"
LIBRARY_DIR="${LITERT_DIR}/linux_arm64"
LITERT_PYTHON="${LITERT_DIR}/.venv/bin/python"

# Config the address, the same one llama-server uses so client.py needs no new URL
HOST="127.0.0.1"
PORT="8080"

# Main
main() {
    # Show usage when asked
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        echo "Usage: ./litert_server.sh"
        exit 0
    fi

    # Quit when LiteRT is not installed
    if [[ ! -x "${LITERT_PYTHON}" ]]; then
        echo "LiteRT not installed. Run ./install_LiteRT.sh first."
        exit 1
    fi

    # Load the prebuilt GPU libraries, then serve in place of this script
    export LD_LIBRARY_PATH="${LIBRARY_DIR}:${LD_LIBRARY_PATH:-}"
    echo "Model: LiteRT Gemma"
    exec "${LITERT_PYTHON}" "${TEXT_DIR}/litert_server.py" --host "${HOST}" --port "${PORT}"
}

# Run
main "$@"
