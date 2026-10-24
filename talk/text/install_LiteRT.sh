#!/usr/bin/env bash

# Install LiteRT-LM and its Gemma model, an alternative to llama.cpp for talk.py, set litert true in config.json

# Stop on errors and unset variables
set -euo pipefail

# Paths
TEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LITERT_DIR="${TEXT_DIR}/litert"
LIBRARY_DIR="${LITERT_DIR}/linux_arm64"
LITERT_BIN="${LITERT_DIR}/.venv/bin/litert-lm"

# Config the package, the Python it needs, and where its prebuilt GPU libraries live
PYTHON_VERSION="3.12"
PACKAGE_NAME="litert-lm"
LIBRARY_BASE_URL="https://media.githubusercontent.com/media/google-ai-edge/LiteRT-LM"
LIBRARY_NAMES=(libGemmaModelConstraintProvider.so libLiteRt.so libLiteRtWebGpuAccelerator.so libwebgpu_dawn.so)

# Config the model that talk.py asks for
MODEL_REPO="litert-community/gemma-4-E2B-it-litert-lm"
MODEL_FILE="gemma-4-E2B-it.litertlm"

# Main
main() {
    # Show usage when asked
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        print_usage
        exit 0
    fi

    # Install each piece, each one is skipped when it is already there
    install_package
    install_libraries
    install_model
    echo "LiteRT ready. Set litert true in talk/config.json, then run ./talk.py from talk."
}

# Print usage
print_usage() {
    echo "Usage: ./install_LiteRT.sh"
    echo "  installs litert-lm, its arm64 GPU libraries, and ${MODEL_FILE} into text/litert"
}

# Create the Python environment and install litert-lm
install_package() {
    # Skip when the command exists
    if [[ -x "${LITERT_BIN}" ]]; then
        return 0
    fi

    # Create the environment and install the package
    echo "Installing ${PACKAGE_NAME}..."
    mkdir -p "${LITERT_DIR}"
    uv venv --python "${PYTHON_VERSION}" "${LITERT_DIR}/.venv"
    uv pip install --python "${LITERT_DIR}/.venv/bin/python" "${PACKAGE_NAME}"
}

# Download the GPU libraries the pip package leaves out, matched to the installed version
install_libraries() {
    local version
    local name

    # Find the package version so the libraries match it
    version="$("${LITERT_DIR}/.venv/bin/python" -c "import importlib.metadata as metadata; print(metadata.version('${PACKAGE_NAME}'))")"
    mkdir -p "${LIBRARY_DIR}"

    # Fetch each missing library
    for name in "${LIBRARY_NAMES[@]}"; do
        if [[ -s "${LIBRARY_DIR}/${name}" ]]; then
            continue
        fi
        echo "Downloading ${name}..."
        curl -fL --progress-bar -o "${LIBRARY_DIR}/${name}" "${LIBRARY_BASE_URL}/v${version}/prebuilt/linux_arm64/${name}"
    done
}

# Download the Gemma model into the litert-lm model store
install_model() {
    # Skip when the model is already imported
    if "${LITERT_BIN}" list 2>/dev/null | grep -q "${MODEL_FILE}"; then
        return 0
    fi

    # Import the model from Hugging Face
    echo "Downloading ${MODEL_FILE}..."
    "${LITERT_BIN}" import --from-huggingface-repo="${MODEL_REPO}" "${MODEL_FILE}"
}

# Run
main "$@"
