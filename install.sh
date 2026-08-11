#!/usr/bin/env bash
# Install dependencies into .venv with uv, works on linux and mac.
# Usage: ./install.sh [--listen] [--talk]

# WiFi connect: 
# nmcli device wifi connect "NETWORK" password "PASSWORD"

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config venv and packages
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_VERSION="3.12"
JETSON_PYTHON_VERSION="3.10"
PYTHON_PACKAGES=(kokoro soundfile soco)
SPACY_MODEL_URL="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
LISTEN_PACKAGES=(faster-whisper)
BUILD_PACKAGES=(pybind11 wheel)

# Config torch, the default linux wheel pulls cuda libraries a raspberry pi cannot load
TORCH_PACKAGES=(torch==2.9.1)
TORCH_JETSON_PACKAGES=(torch==2.8.0 'numpy<2')
TORCH_CPU_INDEX_URL="https://download.pytorch.org/whl/cpu"
TORCH_CUDA_INDEX_URL="https://download.pytorch.org/whl/cu126"
TORCH_JETSON_INDEX_URL="https://pypi.jetson-ai-lab.io/jp6/cu126"
CUDA_PACKAGE_PATTERN="^nvidia-"
DEVICE_TREE_MODEL="/proc/device-tree/model"
RASPBERRY_PI_MATCH="Raspberry Pi"
JETSON_RELEASE_FILE="/etc/nv_tegra_release"

# Config system packages and players
LINUX_PACKAGES=(espeak-ng alsa-utils htop)
MAC_PACKAGES=(espeak-ng)
MAC_LISTEN_PACKAGES=(sox)
LINUX_PLAYER="aplay"
MAC_PLAYER="afplay"
BREW_PATHS=(/opt/homebrew/bin/brew /usr/local/bin/brew)

# Config uv
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
UV_BIN_DIR="${HOME}/.local/bin"

# Config the ctranslate2 cuda build for listen.py
LIBS_DIR="${SCRIPT_DIR}/libs"
FASTER_WHISPER_URL="https://github.com/SYSTRAN/faster-whisper.git"
FASTER_WHISPER_DIR="${LIBS_DIR}/faster-whisper"
CTRANSLATE2_URL="https://github.com/OpenNMT/CTranslate2.git"
CTRANSLATE2_DIR="${LIBS_DIR}/CTranslate2"
CTRANSLATE2_LIBRARY="/usr/local/lib/libctranslate2.so"
CUDA_BIN="/usr/local/cuda/bin"
CUDA_ARCHITECTURE=87
BUILD_JOBS=6
CTRANSLATE2_CXX_WARNINGS="-Wno-unused-parameter -Wno-unused-variable -Wno-implicit-fallthrough -Wno-reorder"
CTRANSLATE2_CUDA_WARNINGS="-diag-suppress=177 -Xcompiler=-Wno-unused-parameter,-Wno-unused-variable,-Wno-reorder"

# Config llama.cpp and the text model
LLAMA_CPP_URL="https://github.com/ggml-org/llama.cpp.git"
TEXT_DIR="${SCRIPT_DIR}/text"
LLAMA_CPP_DIR="${TEXT_DIR}/llama.cpp"
TEXT_MODEL_DIR="${TEXT_DIR}/models"
TEXT_MODEL_NAME="gemma-4-E2B-it-Q4_K_S.gguf"
TEXT_MODEL_PATH="${TEXT_MODEL_DIR}/${TEXT_MODEL_NAME}"
TEXT_MODEL_PART="${TEXT_MODEL_PATH}.part"
TEXT_MODEL_URL="https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/${TEXT_MODEL_NAME}"
LLAMA_SERVER="${LLAMA_CPP_DIR}/build/bin/llama-server"
LLAMA_LIBRARY_DIR="${LLAMA_CPP_DIR}/build/bin"

# State
INSTALL_LISTEN=false
INSTALL_TALK=false

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
    if [[ "${INSTALL_TALK}" == true ]]; then
        install_text
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
            --talk|--text)
                INSTALL_TALK=true
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                echo "Unknown argument: ${argument}"
                print_usage
                exit 1
                ;;
        esac
    done
}

# Print usage help
print_usage() {
    echo "Usage: ./install.sh [--listen] [--talk]"
    echo "  --listen  also install speech to text for listen.py and talk.py"
    echo "  --talk    also install llama.cpp and Gemma 4 E2B for talk.py"
    echo "  (no arg)  install text to speech for speak.py and say.py into .venv"
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

# Create the venv when missing or on the wrong python
create_venv() {
    local python_version
    python_version="$(venv_python_version)"

    # Recreate when an existing venv is on the wrong python for this machine
    if [[ -x "${VENV_DIR}/bin/python" ]]; then
        if "${VENV_DIR}/bin/python" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == tuple(map(int, '${python_version}'.split('.'))) else 1)"; then
            echo "Venv already exists at ${VENV_DIR}."
            return 0
        fi
        echo "Recreating venv for Python ${python_version}."
        rm -rf "${VENV_DIR}"
    fi

    # Create a venv on a python version torch supports
    uv venv --python "${python_version}" "${VENV_DIR}"
}

# Install the python packages into the venv
install_python_packages() {
    # Install torch first so kokoro does not pull the cuda build in as a dependency
    install_torch

    # Install kokoro and its runtime
    install_packages "${PYTHON_PACKAGES[@]}"

    # Install the spacy english model kokoro downloads on first run
    install_packages "${SPACY_MODEL_URL}"
}

# Install torch, Jetson Orin wheels, CPU on pi, CUDA 12.6 elsewhere
install_torch() {
    # Use Jetson AI Lab wheels built for Orin sm_87, pytorch.org aarch64 skips that arch
    if is_jetson; then
        echo "Installing torch ${TORCH_JETSON_PACKAGES[*]} from ${TORCH_JETSON_INDEX_URL}."
        uv pip install --python "${VENV_DIR}/bin/python" --index-url "${TORCH_JETSON_INDEX_URL}" "${TORCH_JETSON_PACKAGES[@]}"
        return 0
    fi

    # Use the CUDA 12.6 index on other CUDA machines
    if ! is_raspberry_pi; then
        echo "Installing torch ${TORCH_PACKAGES[*]} from ${TORCH_CUDA_INDEX_URL}."
        uv pip install --python "${VENV_DIR}/bin/python" --index-url "${TORCH_CUDA_INDEX_URL}" "${TORCH_PACKAGES[@]}"
        return 0
    fi

    # Drop any cuda packages a previous run installed, they are gigabytes of dead weight here
    echo "Raspberry Pi detected, installing CPU torch."
    remove_cuda_packages

    # Install from the cpu index, the default wheel preloads cuda libraries and crashes on import
    uv pip install --python "${VENV_DIR}/bin/python" --index-url "${TORCH_CPU_INDEX_URL}" "${TORCH_PACKAGES[@]}"
}

# Uninstall the nvidia cuda packages when present
remove_cuda_packages() {
    # Skip when no cuda packages are installed
    local installed
    installed="$(uv pip list --python "${VENV_DIR}/bin/python" 2>/dev/null | awk '{print $1}' | grep -E "${CUDA_PACKAGE_PATTERN}" || true)"
    if [[ -z "${installed}" ]]; then
        return 0
    fi

    # Remove them so the venv stays small and torch cannot preload them
    echo "Removing CUDA packages not needed on this machine..."
    # shellcheck disable=SC2086
    uv pip uninstall --python "${VENV_DIR}/bin/python" ${installed}
}

# Quit when the imports or the audio player are unavailable
verify_install() {
    # Check the python packages import
    if ! "${VENV_DIR}/bin/python" -c 'import kokoro, torch, soundfile' >/dev/null 2>&1; then
        echo "Install failed, kokoro, torch, or soundfile did not import."
        exit 1
    fi

    # Check the Jetson wheel can run CUDA kernels for Orin
    if is_jetson; then
        verify_jetson_torch
    fi

    # Check the audio player for this platform
    if ! command -v "$(audio_player)" >/dev/null 2>&1; then
        echo "Audio player $(audio_player) not found, speech cannot play."
        exit 1
    fi
    echo "Verified imports and $(audio_player)."
}

# Quit when torch cannot run a CUDA kernel on this Jetson
verify_jetson_torch() {
    if ! "${VENV_DIR}/bin/python" -c 'import torch; x = torch.randn(8, 8, device="cuda"); x @ x' >/dev/null 2>&1; then
        echo "Install failed, torch cannot run CUDA on this Jetson GPU."
        exit 1
    fi
    echo "CUDA OK on Jetson GPU."
}

# Install faster-whisper so listen.py and talk.py can transcribe
install_listen() {
    # Install sox on mac, listen.py records with it there
    if [[ "$(uname -s)" == "Darwin" ]]; then
        install_mac_packages "${MAC_LISTEN_PACKAGES[@]}"
        install_packages "${LISTEN_PACKAGES[@]}"
        return 0
    fi

    # Install cpu wheels on a raspberry pi, it has no cuda gpu to build against
    if is_raspberry_pi; then
        echo "Raspberry Pi detected, installing CPU wheels."
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
    clone_repository "${CTRANSLATE2_URL}" "${CTRANSLATE2_DIR}" --depth 1 --recursive --shallow-submodules
    clone_repository "${FASTER_WHISPER_URL}" "${FASTER_WHISPER_DIR}" --depth 1
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

    # Configure and build, silence unused-code warnings from upstream sources
    cd "${CTRANSLATE2_DIR}"
    PATH="${CUDA_BIN}:${PATH}" cmake -B build -DCMAKE_BUILD_TYPE=Release -DWITH_CUDA=ON -DWITH_CUDNN=ON -DWITH_MKL=OFF -DWITH_OPENBLAS=ON -DOPENMP_RUNTIME=COMP -DBUILD_CLI=OFF -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURE}" -DCMAKE_CXX_FLAGS="${CTRANSLATE2_CXX_WARNINGS}" -DCUDA_NVCC_FLAGS="${CTRANSLATE2_CUDA_WARNINGS}"
    silence_ctranslate2_cxx_warnings
    PATH="${CUDA_BIN}:${PATH}" cmake --build build "-j${BUILD_JOBS}"
}

# Re-append warning silences after CTranslate2 adds -Wall -Wextra
silence_ctranslate2_cxx_warnings() {
    local flags_file
    while IFS= read -r flags_file; do
        sed -i "s/-Wall -Wextra/-Wall -Wextra ${CTRANSLATE2_CXX_WARNINGS}/g" "${flags_file}"
    done < <(find "${CTRANSLATE2_DIR}/build" -name flags.make)
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

# Install llama.cpp and Gemma for local text responses
install_text() {
    check_build_tools
    clone_repository "${LLAMA_CPP_URL}" "${LLAMA_CPP_DIR}" --depth 1
    download_text_model
    build_llama_cpp
    chmod +x "${TEXT_DIR}/server.sh" "${TEXT_DIR}/ask.py"
    verify_text
}

# Download the Gemma GGUF model with resume support
download_text_model() {
    # Skip when model is already downloaded
    if [[ -f "${TEXT_MODEL_PATH}" ]]; then
        echo "${TEXT_MODEL_NAME} already downloaded."
        return 0
    fi

    # Download to a partial file and rename only after success
    mkdir -p "${TEXT_MODEL_DIR}"
    echo "Downloading ${TEXT_MODEL_NAME}, about 3 GB..."
    curl --fail --location --continue-at - --output "${TEXT_MODEL_PART}" "${TEXT_MODEL_URL}"
    mv "${TEXT_MODEL_PART}" "${TEXT_MODEL_PATH}"
}

# Build llama.cpp for CUDA when available, else CPU
build_llama_cpp() {
    # Skip when llama-server is already built
    if [[ -x "${LLAMA_SERVER}" ]]; then
        echo "llama.cpp already built."
        return 0
    fi

    # Configure CUDA on Jetson or CPU elsewhere
    if [[ -x "${CUDA_BIN}/nvcc" ]] && ! is_raspberry_pi; then
        PATH="${CUDA_BIN}:${PATH}" cmake -B "${LLAMA_CPP_DIR}/build" -S "${LLAMA_CPP_DIR}" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURE}" -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
    else
        cmake -B "${LLAMA_CPP_DIR}/build" -S "${LLAMA_CPP_DIR}" -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
    fi

    # Build only the server, skips cli and bench to cut Jetson compile time
    PATH="${CUDA_BIN}:${PATH}" cmake --build "${LLAMA_CPP_DIR}/build" --target llama-server "-j${BUILD_JOBS}"
}

# Quit when llama-server or the model is unavailable
verify_text() {
    # Check runtime and model
    if [[ ! -x "${LLAMA_SERVER}" || ! -f "${TEXT_MODEL_PATH}" ]]; then
        echo "Text install failed, llama-server or model missing."
        exit 1
    fi

    # Print installed runtime version
    LD_LIBRARY_PATH="${LLAMA_LIBRARY_DIR}:${LD_LIBRARY_PATH:-}" "${LLAMA_SERVER}" --version
    echo "Verified local text model."
}

# Print what to run next
print_done() {
    echo "Done. Run ./speak.py to speak."
    if [[ "${INSTALL_LISTEN}" == true ]]; then
        echo "Run ./listen.py to transcribe from the microphone."
    fi
    if [[ "${INSTALL_TALK}" == true ]]; then
        echo "Run ./talk.py to put speech and the local model together."
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

    # Create the parent directory for clones under libs
    mkdir -p "$(dirname "${directory}")"
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

# Return the python version this machine should use for torch
venv_python_version() {
    if is_jetson; then
        echo "${JETSON_PYTHON_VERSION}"
    else
        echo "${PYTHON_VERSION}"
    fi
}

# Return true when running on a Jetson
is_jetson() {
    # L4T ships this release file on Jetson boards
    if [[ -r "${JETSON_RELEASE_FILE}" ]]; then
        return 0
    fi
    return 1
}

# Return true when running on a raspberry pi
is_raspberry_pi() {
    # Read the board name the firmware exposes, missing on other machines
    if [[ -r "${DEVICE_TREE_MODEL}" ]] && tr -d '\0' < "${DEVICE_TREE_MODEL}" | grep -q "${RASPBERRY_PI_MATCH}"; then
        return 0
    fi
    return 1
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
