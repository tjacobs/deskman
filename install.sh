#!/usr/bin/env bash
# Install dependencies into .venv with uv, works on linux and mac.
# Usage: ./install.sh [--listen]

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config venv and packages
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_VERSION="3.12"
PYTHON_PACKAGES=(kokoro soundfile torch)
SPACY_MODEL_URL="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
LISTEN_PACKAGES=(faster-whisper)
BUILD_PACKAGES=(pybind11 wheel)

# Config system packages and players
LINUX_PACKAGES=(espeak-ng alsa-utils)
MAC_PACKAGES=(espeak-ng)
MAC_LISTEN_PACKAGES=(sox)
LINUX_PLAYER="aplay"
MAC_PLAYER="afplay"
BREW_PATHS=(/opt/homebrew/bin/brew /usr/local/bin/brew)

# Config uv
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
UV_BIN_DIR="${HOME}/.local/bin"

# Config the ctranslate2 cuda build for listen.py
FASTER_WHISPER_URL="https://github.com/SYSTRAN/faster-whisper.git"
FASTER_WHISPER_DIR="${HOME}/faster-whisper"
CTRANSLATE2_URL="https://github.com/OpenNMT/CTranslate2.git"
CTRANSLATE2_DIR="${HOME}/CTranslate2"
CTRANSLATE2_LIBRARY="/usr/local/lib/libctranslate2.so"
CUDA_BIN="/usr/local/cuda/bin"
CUDA_ARCHITECTURE=87
BUILD_JOBS=3

# State
INSTALL_LISTEN=false

# Main
main() {
    parse_args "$@"
    install_uv
    install_system_packages
    create_venv
    install_python_packages
    verify_install
    if [[ "${INSTALL_LISTEN}" == true ]]; then
        install_listen
    fi
    print_done
}

# Parse command line arguments
parse_args() {
    for argument in "$@"; do
        case "${argument}" in
            --listen)
                INSTALL_LISTEN=true
                ;;
            *)
                echo "Unknown argument: ${argument}"
                echo "Usage: ./install.sh [--listen]"
                exit 1
                ;;
        esac
    done
}

# Install uv when missing
install_uv() {
    # Skip when already installed
    if command -v uv >/dev/null 2>&1; then
        echo "uv already installed, $(uv --version)."
        return 0
    fi

    # Download uv and add it to the path for this run
    echo "Installing uv..."
    curl -LsSf "${UV_INSTALL_URL}" | sh
    export PATH="${UV_BIN_DIR}:${PATH}"
}

# Install the espeak and audio packages for this platform
install_system_packages() {
    # Pick the package manager for this platform
    if [[ "$(uname -s)" == "Darwin" ]]; then
        install_mac_packages "${MAC_PACKAGES[@]}"
    else
        install_linux_packages "${LINUX_PACKAGES[@]}"
    fi
}

# Install mac packages with homebrew
install_mac_packages() {
    # Quit when homebrew is missing
    local brew_command
    brew_command="$(find_brew)"
    if [[ -z "${brew_command}" ]]; then
        echo "Homebrew not found. Install it from https://brew.sh, then run again."
        exit 1
    fi

    # Install each package when missing
    for package in "$@"; do
        if "${brew_command}" list --formula "${package}" >/dev/null 2>&1; then
            echo "${package} already installed."
            continue
        fi
        "${brew_command}" install "${package}"
    done
}

# Install linux packages with apt
install_linux_packages() {
    # Quit when apt is missing
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "apt-get not found. Install $* with your package manager, then run again."
        exit 1
    fi

    # Skip when every package is already installed
    local missing=()
    for package in "$@"; do
        if ! dpkg -s "${package}" >/dev/null 2>&1; then
            missing+=("${package}")
        fi
    done
    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "System packages already installed."
        return 0
    fi

    # Install the missing packages
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
}

# Create the venv when missing
create_venv() {
    # Skip when the venv already exists
    if [[ -x "${VENV_DIR}/bin/python" ]]; then
        echo "Venv already exists at ${VENV_DIR}."
        return 0
    fi

    # Create a venv on a python version torch supports
    uv venv --python "${PYTHON_VERSION}" "${VENV_DIR}"
}

# Install the python packages into the venv
install_python_packages() {
    # Install kokoro and its runtime
    install_packages "${PYTHON_PACKAGES[@]}"

    # Install the spacy english model kokoro downloads on first run
    install_packages "${SPACY_MODEL_URL}"
}

# Quit when the imports or the audio player are unavailable
verify_install() {
    # Check the python packages import
    if ! "${VENV_DIR}/bin/python" -c 'import kokoro, torch, soundfile' >/dev/null 2>&1; then
        echo "Install failed, kokoro, torch, or soundfile did not import."
        exit 1
    fi

    # Check the audio player for this platform
    if ! command -v "$(audio_player)" >/dev/null 2>&1; then
        echo "Audio player $(audio_player) not found, speech cannot play."
        exit 1
    fi
    echo "Verified imports and $(audio_player)."
}

# Install faster-whisper so listen.py and talk.py can transcribe
install_listen() {
    # Install sox on mac, listen.py records with it there
    if [[ "$(uname -s)" == "Darwin" ]]; then
        install_mac_packages "${MAC_LISTEN_PACKAGES[@]}"
        install_packages "${LISTEN_PACKAGES[@]}"
        return 0
    fi

    # Build ctranslate2 with cuda when the toolkit is present, else install cpu wheels
    if [[ -x "${CUDA_BIN}/nvcc" ]]; then
        install_listen_cuda
    else
        echo "CUDA toolkit not found at ${CUDA_BIN}, installing CPU wheels."
        install_packages "${LISTEN_PACKAGES[@]}"
    fi
}

# Build and install faster-whisper against a cuda build of ctranslate2
install_listen_cuda() {
    check_build_tools
    clone_repository "${CTRANSLATE2_URL}" "${CTRANSLATE2_DIR}" --recursive
    clone_repository "${FASTER_WHISPER_URL}" "${FASTER_WHISPER_DIR}"
    build_ctranslate2
    install_ctranslate2
    install_listen_packages
    verify_cuda
}

# Quit early when the build tools are missing
check_build_tools() {
    for tool in cmake git; do
        if ! command -v "${tool}" >/dev/null 2>&1; then
            echo "${tool} not found. Install with: sudo apt install ${tool}"
            exit 1
        fi
    done
}

# Build ctranslate2 with cuda for the jetson gpu, takes around 30 minutes
build_ctranslate2() {
    # Skip when already built
    if [[ -n "$(ls "${CTRANSLATE2_DIR}/build/libctranslate2.so"* 2>/dev/null)" ]]; then
        echo "CTranslate2 already built."
        return 0
    fi

    # Configure and build
    cd "${CTRANSLATE2_DIR}"
    PATH="${CUDA_BIN}:${PATH}" cmake -B build -DCMAKE_BUILD_TYPE=Release -DWITH_CUDA=ON -DWITH_CUDNN=ON -DWITH_MKL=OFF -DWITH_OPENBLAS=ON -DOPENMP_RUNTIME=COMP -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURE}"
    PATH="${CUDA_BIN}:${PATH}" cmake --build build "-j${BUILD_JOBS}"
}

# Install the ctranslate2 library system wide
install_ctranslate2() {
    # Skip when already installed
    if [[ -f "${CTRANSLATE2_LIBRARY}" ]]; then
        echo "CTranslate2 library already installed."
        return 0
    fi
    sudo cmake --install "${CTRANSLATE2_DIR}/build"
    sudo ldconfig
}

# Install the local ctranslate2 wrapper and faster-whisper into the venv
install_listen_packages() {
    # Install the build tools the ctranslate2 wrapper needs
    install_packages "${BUILD_PACKAGES[@]}"

    # Build the wrapper against the local library, not the cpu wheel from pypi
    uv pip install --python "${VENV_DIR}/bin/python" --no-build-isolation "${CTRANSLATE2_DIR}/python"

    # Install faster-whisper from the local clone
    install_packages "${FASTER_WHISPER_DIR}"
}

# Quit when the installed ctranslate2 cannot see the gpu
verify_cuda() {
    local device_count
    device_count="$(cd "${SCRIPT_DIR}" && "${VENV_DIR}/bin/python" -c 'import ctranslate2; print(ctranslate2.get_cuda_device_count())' 2>/dev/null || echo 0)"
    if [[ "${device_count}" -lt 1 ]]; then
        echo "CUDA verification failed, ctranslate2 sees no GPU."
        exit 1
    fi
    echo "CUDA OK, ${device_count} GPU found."
}

# Print what to run next
print_done() {
    echo "Done. Run ./speak.py to speak."
    if [[ "${INSTALL_LISTEN}" == true ]]; then
        echo "Run ./listen.py to transcribe from the microphone."
    fi
}

# Install packages into the venv
install_packages() {
    uv pip install --python "${VENV_DIR}/bin/python" "$@"
}

# Clone a repository when missing, extra arguments pass to git clone
clone_repository() {
    local url="$1"
    local directory="$2"
    shift 2

    # Skip when already cloned
    if [[ -d "${directory}" ]]; then
        echo "$(basename "${directory}") already cloned."
        return 0
    fi
    git clone "$@" "${url}" "${directory}"
}

# Return path to brew, empty when missing
find_brew() {
    # Use brew from the path when present
    if command -v brew 2>/dev/null; then
        return 0
    fi

    # Fall back to the install locations, the path misses brew in non login shells
    for path in "${BREW_PATHS[@]}"; do
        if [[ -x "${path}" ]]; then
            echo "${path}"
            return 0
        fi
    done
}

# Return audio player command for this platform
audio_player() {
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "${MAC_PLAYER}"
    else
        echo "${LINUX_PLAYER}"
    fi
}

main "$@"
