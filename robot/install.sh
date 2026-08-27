#!/usr/bin/env bash
# Install SDL and image libraries for the robot.

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
}

# Install Linux packages with apt
install_linux() {
    export DEBIAN_FRONTEND=noninteractive

    # Refresh package lists
    sudo apt-get update -y

    # Install SDL and image build deps
    sudo apt-get install -y pkg-config libsdl2-dev libsdl2-image-dev libsdl2-ttf-dev libjpeg-dev libpng-dev libwebp-dev libcurl4-openssl-dev
}

# Install macOS packages with Homebrew
install_mac() {
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required on macOS. Install from https://brew.sh" >&2
        exit 1
    fi

    # Install SDL and image libs
    brew install pkg-config sdl2 sdl2_image sdl2_ttf jpeg libpng webp curl
}

# Run install
main "$@"
