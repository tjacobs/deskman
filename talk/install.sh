#!/usr/bin/env bash
# Install dependencies into .venv with uv, works on linux and mac.
# Usage: ./install.sh

# WiFi connect: 
# nmcli device wifi connect "NETWORK" password "PASSWORD"

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Load the shared sudo, disk space, and cleanup helpers
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../install.sh
source "${SCRIPT_DIR}/../install.sh"

# Config venv and packages
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_VERSION="3.12"
JETSON_PYTHON_VERSION="3.10"

# Pin transformers so tokenizers comes from a wheel, 4.12 builds from source and needs Rust
PYTHON_PACKAGES=(kokoro soundfile soco websocket-client 'transformers>=4.46,<5')
SPACY_MODEL_URL="https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
LISTEN_PACKAGES=(faster-whisper)
BUILD_PACKAGES=(pybind11 wheel)

# Config torch, the default linux wheel pulls cuda libraries a raspberry pi cannot load
TORCH_PACKAGES=(torch==2.9.1)
TORCH_JETSON_PACKAGES=(torch==2.8.0 'numpy<2')
TORCH_JETPACK7_PACKAGES=(torch==2.12.0)
TORCH_CPU_INDEX_URL="https://download.pytorch.org/whl/cpu"
TORCH_CUDA_INDEX_URL="https://download.pytorch.org/whl/cu126"
TORCH_JETSON_INDEX_URL="https://pypi.jetson-ai-lab.io/jp6/cu126"
TORCH_JETPACK7_INDEX_URL="https://download.pytorch.org/whl/cu132"
JETPACK7_L4T_RELEASE=39
CUDA_PACKAGE_PATTERN="^nvidia-"

# Config a torch dependency left out of the venv, it only compiles kernels
EXCLUDED_PACKAGE="triton"

# Config memory each CUDA compile job needs, free memory decides how many run at once
CUDA_BUILD_MEMORY_PER_JOB_MB=768
DEVICE_TREE_MODEL="/proc/device-tree/model"
RASPBERRY_PI_MATCH="Raspberry Pi"
JETSON_RELEASE_FILE="/etc/nv_tegra_release"

# Config system packages and players, cmake and a compiler build the local model
LINUX_PACKAGES=(espeak-ng alsa-utils htop zenity wl-clipboard xsel build-essential cmake libssl-dev)
MAC_PACKAGES=(espeak-ng)
MAC_LISTEN_PACKAGES=(sox)
LINUX_PLAYER="aplay"
MAC_PLAYER="afplay"
BREW_PATHS=(/opt/homebrew/bin/brew /usr/local/bin/brew)

# Config the JetPack cuda toolkit, cudnn, and openblas, nvcc builds llama.cpp and ctranslate2 for the gpu
JETSON_CUDA_PACKAGES=(nvidia-cuda-dev nvidia-cudnn-dev libopenblas-dev)

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
BUILD_JOBS="$(build_jobs)"
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
LLAMA_CUDA_LIBRARY="${LLAMA_LIBRARY_DIR}/libggml-cuda.so"

# State, speech to text and the local model install by default
INSTALL_LISTEN=true
INSTALL_TALK=true

# State the power mode to put back after the CUDA compile
SAVED_POWER_MODE=""

# Main
main() {
    # Say which install section is running
    echo ""
    echo "======== Talk ========"

    parse_args "$@"
    prime_sudo

    # Put the power mode back when the script exits, including a failed compile
    trap restore_build_power_mode EXIT

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

    # Put the power mode back before the rest of the install
    restore_build_power_mode
    free_disk_space
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
    echo "  (no arg)  install text to speech, speech to text, llama.cpp, and Gemma 4 E2B"
    echo "  --listen  install speech to text for listen.py and talk.py"
    echo "  --talk    install llama.cpp and Gemma 4 E2B for talk.py"
}

# Return true when the ctranslate2 library is installed and sees the gpu
ctranslate2_ready() {
    if [[ ! -f "${CTRANSLATE2_LIBRARY}" ]]; then
        return 1
    fi

    # Ignore a native crash, it is not ready and must not dump a core
    if (ulimit -c 0; cd "${SCRIPT_DIR}" && "${VENV_DIR}/bin/python" -c 'import ctranslate2, sys; sys.exit(0 if ctranslate2.get_cuda_device_count() > 0 else 1)') >/dev/null 2>&1; then
        return 0
    fi
    return 1
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

    # Install the cuda toolkit on a Jetson so the builds use the gpu
    if is_jetson; then
        install_jetson_cuda
    fi
}

# Install the JetPack cuda toolkit and quit when nvcc is still missing
install_jetson_cuda() {
    local package
    local packages=()

    # Skip the toolkit metapackage once nvcc is present, installing it again brings Nsight back
    for package in "${JETSON_CUDA_PACKAGES[@]}"; do
        if [[ "${package}" == "nvidia-cuda-dev" && -x "${CUDA_BIN}/nvcc" ]]; then
            continue
        fi
        packages+=("${package}")
    done
    if [[ ${#packages[@]} -gt 0 ]]; then
        install_linux_packages "${packages[@]}"
    fi
    if [[ ! -x "${CUDA_BIN}/nvcc" ]]; then
        echo "CUDA toolkit install failed, ${CUDA_BIN}/nvcc not found."
        exit 1
    fi
    echo "CUDA toolkit OK, $("${CUDA_BIN}/nvcc" --version | tail -n 1)."
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

    # Install the missing packages without prompts
    apt_get update
    apt_get install -y "${missing[@]}"

    # Delete the downloaded package files, the CUDA toolkit alone leaves gigabytes behind
    clean_apt_cache
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

# Install torch, JetPack 7 CUDA 13.2 wheels, JetPack 6 Orin wheels, CPU on pi, CUDA 12.6 elsewhere
install_torch() {
    # JetPack 7 has no system libcudart.so.12, so use the upstream wheel that ships CUDA 13.2
    if is_jetpack7; then
        echo "Installing torch ${TORCH_JETPACK7_PACKAGES[*]} from ${TORCH_JETPACK7_INDEX_URL}."
        install_packages --index-url "${TORCH_JETPACK7_INDEX_URL}" "${TORCH_JETPACK7_PACKAGES[@]}"
        return 0
    fi

    # Use Jetson AI Lab wheels built for Orin sm_87, pytorch.org aarch64 skips that arch
    if is_jetson; then
        echo "Installing torch ${TORCH_JETSON_PACKAGES[*]} from ${TORCH_JETSON_INDEX_URL}."
        install_packages --index-url "${TORCH_JETSON_INDEX_URL}" "${TORCH_JETSON_PACKAGES[@]}"
        return 0
    fi

    # Use the CUDA 12.6 index on other CUDA machines
    if ! is_raspberry_pi; then
        echo "Installing torch ${TORCH_PACKAGES[*]} from ${TORCH_CUDA_INDEX_URL}."
        install_packages --index-url "${TORCH_CUDA_INDEX_URL}" "${TORCH_PACKAGES[@]}"
        return 0
    fi

    # Drop any cuda packages a previous run installed, they are gigabytes of dead weight here
    echo "Raspberry Pi detected, installing CPU torch."
    remove_cuda_packages

    # Install from the cpu index, the default wheel preloads cuda libraries and crashes on import
    install_packages --index-url "${TORCH_CPU_INDEX_URL}" "${TORCH_PACKAGES[@]}"
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
    uv pip uninstall --quiet --python "${VENV_DIR}/bin/python" ${installed}
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

# Install faster-whisper against a cuda build of ctranslate2
install_listen_cuda() {
    # Skip when the library and the venv already see the gpu
    if ctranslate2_ready; then
        echo "CTranslate2 already installed."
        return 0
    fi

    # Build, install, and check the gpu works
    check_build_tools
    clone_repository "${CTRANSLATE2_URL}" "${CTRANSLATE2_DIR}" --depth 1 --recursive --shallow-submodules
    clone_repository "${FASTER_WHISPER_URL}" "${FASTER_WHISPER_DIR}" --depth 1
    build_ctranslate2
    install_ctranslate2
    install_listen_packages
    verify_cuda

    # Delete the sources and build, the install copied what it needs
    remove_listen_sources
}

# Delete the ctranslate2 and faster-whisper clones and build, they are gigabytes on a small SD card
remove_listen_sources() {
    # Leave the folder first, the build step moved the shell into it
    cd "${SCRIPT_DIR}"
    rm -rf "${CTRANSLATE2_DIR}" "${FASTER_WHISPER_DIR}"
    rmdir "${LIBS_DIR}" 2>/dev/null || true
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

# Raise Jetson clocks before a long CUDA compile
set_build_power_mode() {
    # Skip boards that have no Jetson power modes
    if ! is_jetson; then
        return 0
    fi

    # Skip when the power tool is missing
    if [[ ! -x "${SCRIPT_DIR}/tools/power.sh" ]]; then
        return 0
    fi

    # Keep the mode that was active before the first compile
    if [[ -z "${SAVED_POWER_MODE}" ]]; then
        SAVED_POWER_MODE="$(current_power_mode)"
    fi

    # Lock the high clocks for the build
    echo "Setting Jetson power mode to max for the build."
    "${SCRIPT_DIR}/tools/power.sh" max
}

# Return min, mid, or max for the active nvpmodel id
current_power_mode() {
    # Read the active power mode id
    local mode_id
    mode_id="$(nvpmodel -q 2>/dev/null | awk '/^[0-9]+$/{print; exit}')"
    if [[ "${mode_id}" == "0" ]]; then
        echo "min"
        return 0
    fi
    if [[ "${mode_id}" == "2" ]]; then
        echo "max"
        return 0
    fi

    # Use the balanced mode when the id is mid or unreadable
    echo "mid"
}

# Put the Jetson power mode back after the compile
restore_build_power_mode() {
    # Skip when this install did not change the mode
    if [[ -z "${SAVED_POWER_MODE}" ]]; then
        return 0
    fi

    # Apply the mode that was active before the build
    echo "Restoring Jetson power mode to ${SAVED_POWER_MODE}."
    "${SCRIPT_DIR}/tools/power.sh" "${SAVED_POWER_MODE}"
    SAVED_POWER_MODE=""
}

# Return how many compile jobs fit in free memory, at most the CPU count
cuda_build_jobs() {
    local cpu_count available_megabytes jobs

    # Use every cpu where memory is not readable
    cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
    if [[ ! -r /proc/meminfo ]]; then
        echo "${cpu_count}"
        return 0
    fi

    # Size the job count from memory that is free now
    available_megabytes="$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)"
    jobs=$(( available_megabytes / CUDA_BUILD_MEMORY_PER_JOB_MB ))
    if (( jobs > cpu_count )); then
        jobs="${cpu_count}"
    fi
    if (( jobs < 1 )); then
        jobs=1
    fi
    echo "${jobs}"
}

# Build ctranslate2 with cuda for the jetson gpu, takes around 30 minutes
build_ctranslate2() {
    # Skip when already built
    if [[ -n "$(ls "${CTRANSLATE2_DIR}/build/libctranslate2.so"* 2>/dev/null)" ]]; then
        echo "CTranslate2 already built."
        return 0
    fi

    # Raise clocks, then configure and build
    set_build_power_mode
    cd "${CTRANSLATE2_DIR}"
    PATH="${CUDA_BIN}:${PATH}" cmake -B build -DCMAKE_BUILD_TYPE=Release -DWITH_CUDA=ON -DWITH_CUDNN=ON -DWITH_MKL=OFF -DWITH_OPENBLAS=ON -DOPENMP_RUNTIME=COMP -DBUILD_CLI=OFF -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURE}" -DCMAKE_CXX_FLAGS="${CTRANSLATE2_CXX_WARNINGS}" -DCUDA_NVCC_FLAGS="${CTRANSLATE2_CUDA_WARNINGS}" -DCMAKE_POLICY_DEFAULT_CMP0146=OLD
    silence_ctranslate2_cxx_warnings

    # Compile with as many jobs as free memory allows
    local jobs
    jobs="$(cuda_build_jobs)"
    echo "Building CTranslate2 with ${jobs} jobs."
    PATH="${CUDA_BIN}:${PATH}" cmake --build build "-j${jobs}"
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
    echo "CTranslate2 library installed."
}

# Install the local ctranslate2 wrapper and faster-whisper into the venv
install_listen_packages() {
    # Install the build tools the ctranslate2 wrapper needs
    install_packages "${BUILD_PACKAGES[@]}"

    # Build the wrapper against the local library, not the cpu wheel from pypi
    install_packages --no-build-isolation "${CTRANSLATE2_DIR}/python"

    # Install faster-whisper from the local clone
    install_packages "${FASTER_WHISPER_DIR}"
}

# Return how many GPUs the installed ctranslate2 sees, zero when it fails to load
cuda_device_count() {
    # Ignore a native crash so a bad import counts as no GPU and writes no core
    (ulimit -c 0; cd "${SCRIPT_DIR}" && "${VENV_DIR}/bin/python" -c 'import ctranslate2; print(ctranslate2.get_cuda_device_count())' || echo 0) 2>/dev/null
}

# Quit when the installed ctranslate2 cannot see the gpu
verify_cuda() {
    local device_count
    device_count="$(cuda_device_count)"
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
    remove_llama_objects
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
    # Skip when llama-server is built, a Jetson also needs the cuda library so a cpu build is redone
    if [[ -x "${LLAMA_SERVER}" ]] && { ! is_jetson || [[ -e "${LLAMA_CUDA_LIBRARY}" ]]; }; then
        echo "llama.cpp already built."
        return 0
    fi

    # Raise clocks before the compile
    set_build_power_mode

    # Configure CUDA on Jetson or CPU elsewhere
    if [[ -x "${CUDA_BIN}/nvcc" ]] && ! is_raspberry_pi; then
        PATH="${CUDA_BIN}:${PATH}" cmake -B "${LLAMA_CPP_DIR}/build" -S "${LLAMA_CPP_DIR}" -DGGML_CUDA=ON -DGGML_CUDA_NCCL=OFF -DCMAKE_CUDA_COMPILER="${CUDA_BIN}/nvcc" -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURE}" -DGGML_CCACHE=OFF -DCMAKE_BUILD_TYPE=Release
    else
        cmake -B "${LLAMA_CPP_DIR}/build" -S "${LLAMA_CPP_DIR}" -DGGML_CCACHE=OFF -DCMAKE_BUILD_TYPE=Release
    fi

    # Build only the server, skips cli and bench to cut Jetson compile time
    local jobs
    jobs="$(cuda_build_jobs)"
    echo "Building llama-server with ${jobs} jobs."
    PATH="${CUDA_BIN}:${PATH}" cmake --build "${LLAMA_CPP_DIR}/build" --target llama-server "-j${jobs}"
}

# Delete the compiled object files, llama-server and its libraries stay in build/bin
remove_llama_objects() {
    find "${LLAMA_CPP_DIR}/build" -name '*.o' -delete
}

# Quit when llama-server or the model is unavailable
verify_text() {
    # Check runtime and model
    if [[ ! -x "${LLAMA_SERVER}" || ! -f "${TEXT_MODEL_PATH}" ]]; then
        echo "Text install failed, llama-server or model missing."
        exit 1
    fi

    # Check a Jetson build has the cuda library, else the model runs slowly on the cpu
    if is_jetson && [[ ! -e "${LLAMA_CUDA_LIBRARY}" ]]; then
        echo "Text install failed, llama.cpp built without CUDA, ${LLAMA_CUDA_LIBRARY} missing."
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
    # Leave Triton out, torch lists it but nothing here compiles kernels
    uv pip install --quiet --python "${VENV_DIR}/bin/python" --excludes <(printf '%s\n' "${EXCLUDED_PACKAGE}") "$@"
}

# Clone a repository when missing, extra arguments pass to git clone
clone_repository() {
    local url="$1"
    local directory="$2"
    shift 2

    # Skip a checkout git can still read
    if git -C "${directory}" rev-parse --verify HEAD >/dev/null 2>&1; then
        echo "$(basename "${directory}") already cloned."
        return 0
    fi

    # Replace a checkout left empty when the disk filled up
    if [[ -d "${directory}" ]]; then
        echo "Replacing broken $(basename "${directory}") checkout."
        rm -rf "${directory}"
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

# Return the L4T release number, empty when this is not a Jetson
jetson_l4t_release() {
    # First line starts with # R and the release number
    if [[ ! -r "${JETSON_RELEASE_FILE}" ]]; then
        return 0
    fi
    sed -n 's/^# R\([0-9][0-9]*\).*/\1/p' "${JETSON_RELEASE_FILE}" | head -n 1
}

# Return true on JetPack 7, L4T R39 and newer
is_jetpack7() {
    local release
    release="$(jetson_l4t_release)"
    if [[ -n "${release}" && "${release}" -ge "${JETPACK7_L4T_RELEASE}" ]]; then
        return 0
    fi
    return 1
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
