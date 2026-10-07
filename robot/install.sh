#!/usr/bin/env bash
# Install robot libraries, then build the robot binary.

# Stop on errors
set -euo pipefail

# Prefer Ubuntu's ffmpeg on a Jetson, the NVIDIA repo build has no camera or microphone input, a priority over 1000 lets apt replace it
JETSON_RELEASE_FILE="/etc/nv_tegra_release"
FFMPEG_PIN_PATH="/etc/apt/preferences.d/deskman-ffmpeg"
FFMPEG_PIN_PRIORITY=1001

# Main
main() {
    os_name="$(uname -s)"
    case "$os_name" in
        Linux) install_linux ;;
        Darwin) install_mac ;;
        *) echo "Unsupported OS: $os_name" >&2; exit 1 ;;
    esac

    # Compile the robot binary
    build_robot
}

# Install Linux packages with apt
install_linux() {
    export DEBIAN_FRONTEND=noninteractive

    # Keep the ffmpeg that can record, then refresh package lists
    prefer_ubuntu_ffmpeg
    sudo apt-get update -y

    # Install SDL, OpenCV, GStreamer, the compiler, and ffmpeg for video recording
    sudo apt-get install -y --allow-downgrades build-essential cmake pkg-config libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libjpeg-dev libpng-dev libwebp-dev libcurl4-openssl-dev libopencv-dev libgstreamer1.0-dev i2c-tools ffmpeg

    # Let this user open the servo serial port
    sudo usermod -aG dialout "${SUDO_USER:-$(id -un)}"
}

# Pin ffmpeg to Ubuntu on a Jetson, so recording gets the v4l2 camera and alsa microphone inputs
prefer_ubuntu_ffmpeg() {
    if [[ ! -f "${JETSON_RELEASE_FILE}" ]]; then
        return
    fi
    sudo bash -c "printf '%s\n' 'Package: ffmpeg' 'Pin: release o=Ubuntu' 'Pin-Priority: ${FFMPEG_PIN_PRIORITY}' > '${FFMPEG_PIN_PATH}'"
}

# Install macOS packages with Homebrew
install_mac() {
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required on macOS. Install from https://brew.sh" >&2
        exit 1
    fi

    # Install SDL, OpenCV, the compiler, and ffmpeg for video recording
    brew install cmake pkg-config sdl2 sdl2_image sdl2_ttf opencv jpeg libpng webp curl ffmpeg
}

# Configure and compile into build
build_robot() {
    local project_dir
    project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cmake -S "${project_dir}" -B "${project_dir}/build"
    cmake --build "${project_dir}/build"
}

# Run install
main "$@"
