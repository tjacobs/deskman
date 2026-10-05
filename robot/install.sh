#!/usr/bin/env bash
# Install robot libraries, then build the robot binary.

# Stop on errors
set -euo pipefail

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

    # Refresh package lists
    sudo apt-get update -y

    # Install SDL, OpenCV, GStreamer, and the compiler
    sudo apt-get install -y build-essential cmake pkg-config libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libjpeg-dev libpng-dev libwebp-dev libcurl4-openssl-dev libopencv-dev libgstreamer1.0-dev i2c-tools
}

# Install macOS packages with Homebrew
install_mac() {
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required on macOS. Install from https://brew.sh" >&2
        exit 1
    fi

    # Install SDL, OpenCV, and the compiler
    brew install cmake pkg-config sdl2 sdl2_image sdl2_ttf opencv jpeg libpng webp curl
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
