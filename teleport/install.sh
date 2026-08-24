#!/bin/bash

# Exit on error
set -e

# Detect OS
OS="$(uname -s)"

# Install macOS packages
if [ "$OS" = "Darwin" ]; then
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew is required on macOS: https://brew.sh"
        exit 1
    fi
    brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav json-glib libnice libnice-gstreamer x264
    exit 0
fi

# Install Linux packages
if [ "$OS" = "Linux" ]; then
    sudo apt update
    sudo apt install -y build-essential cmake pkg-config nodejs gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-nice libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstreamer-plugins-bad1.0-dev libjson-glib-dev libnice-dev libx264-dev
    if apt-cache show nvidia-l4t-gstreamer >/dev/null 2>&1; then
        sudo apt install -y nvidia-l4t-gstreamer nvidia-l4t-multimedia nvidia-l4t-multimedia-utils
    fi
    exit 0
fi

# Unsupported OS
echo "Unsupported OS: $OS"
exit 1
