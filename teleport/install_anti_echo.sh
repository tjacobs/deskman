#!/bin/bash

# Build the AEC3 echo canceller. It is better than the jetson default decade old AEC.

# Exit on error
set -e

# Build beside this script, so the sources stay with the project
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/aec3"
LOG_FILE="${WORK_DIR}/install.log"

# Library and plugin to build
LIBRARY_VERSION="1.3"
LIBRARY_URL="http://deb.debian.org/debian/pool/main/w/webrtc-audio-processing/webrtc-audio-processing_${LIBRARY_VERSION}.orig.tar.gz"
PLUGIN_TAG="1.24.0"
PLUGIN_URL="https://raw.githubusercontent.com/GStreamer/gstreamer/${PLUGIN_TAG}/subprojects/gst-plugins-bad/ext/webrtcdsp"
PLUGIN_DIR="/usr/local/lib/deskman-gstreamer-1.0"

# How often to refresh the sudo timestamp while the build runs
SUDO_KEEP_ALIVE_SECONDS=60

# How many log lines to show when a step fails
LOG_TAIL_LINES=20

# Main
main() {
    parse_args "$@"
    check_linux
    start_log
    ask_for_password
    install_build_tools
    build_library
    install_library
    fetch_plugin_sources
    build_plugin
    install_plugin
    verify_plugin
}

# Parse command line arguments
parse_args() {
    for argument in "$@"; do

        # Print help and quit
        if [ "$argument" = "-h" ] || [ "$argument" = "--help" ]; then
            echo "Usage: ./install_anti_echo.sh"
            echo "  Build the AEC3 echo canceller into ${WORK_DIR}"
            exit 0
        fi

        # Reject anything else
        echo "Unknown argument: $argument" >&2
        exit 1
    done
}

# Only Linux has the distro GStreamer this works around
check_linux() {
    if [ "$(uname -s)" != "Linux" ]; then
        echo "This script only supports Linux" >&2
        exit 1
    fi
}

# Send the build chatter to a log, the terminal only gets the steps
start_log() {
    mkdir -p "$WORK_DIR"
    : > "$LOG_FILE"
    echo "Logging to $LOG_FILE"
}

# Ask once up front, installing the library and plugin later needs root
ask_for_password() {
    sudo -v

    # Hold the timestamp open, the build outlasts the sudo timeout
    while true; do
        sleep "$SUDO_KEEP_ALIVE_SECONDS"
        kill -0 "$$" 2>/dev/null || exit
        sudo -n true 2>/dev/null || exit
    done &
}

# Meson 0.63 or newer is required and distros often ship older, so use pip
install_build_tools() {
    echo "Installing build tools..."
    run_quiet pip3 install --user --quiet --upgrade meson ninja
    export PATH="$HOME/.local/bin:$PATH"
}

# Build the new audio processing library, it carries its own abseil subproject
build_library() {
    echo "Building webrtc-audio-processing $LIBRARY_VERSION..."
    cd "$WORK_DIR"

    # Download the sources once
    if [ ! -d "webrtc-audio-processing-$LIBRARY_VERSION" ]; then
        run_quiet curl -fsSL -o library.tar.gz "$LIBRARY_URL"
        run_quiet tar xf library.tar.gz
        rm library.tar.gz
    fi

    # Configure and compile
    cd "webrtc-audio-processing-$LIBRARY_VERSION"
    if [ ! -d build ]; then
        run_quiet meson setup build --prefix=/usr/local --buildtype=release -Ddefault_library=shared
    fi
    run_quiet ninja -C build
}

# Install the library, its soname differs from the old one so nothing is replaced
install_library() {
    echo "Installing library..."
    cd "$WORK_DIR/webrtc-audio-processing-$LIBRARY_VERSION"
    USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())')"
    run_quiet sudo env "PATH=$PATH" "PYTHONPATH=$USER_SITE" meson install -C build
    run_quiet sudo ldconfig
}

# Fetch the plugin sources, 1.24 is the first release ported to this library
fetch_plugin_sources() {
    echo "Fetching webrtcdsp $PLUGIN_TAG sources..."
    mkdir -p "$WORK_DIR/webrtcdsp"
    cd "$WORK_DIR/webrtcdsp"
    for FILE in gstwebrtcdsp.cpp gstwebrtcdsp.h gstwebrtcdspplugin.cpp gstwebrtcechoprobe.cpp gstwebrtcechoprobe.h; do
        run_quiet curl -fsSL -o "$FILE" "$PLUGIN_URL/$FILE"
    done

    # Stand in for the gst-plugins-bad build config the sources expect
    cat > config.h <<'EOF'
#define VERSION "1.24.0"
#define PACKAGE "gst-plugins-bad"
#define PACKAGE_VERSION VERSION
#define GST_LICENSE "LGPL"
#define GST_PACKAGE_NAME "GStreamer Bad Plug-ins, AEC3 rebuild"
#define GST_PACKAGE_ORIGIN "https://gstreamer.freedesktop.org"
EOF

    # Build the plugin against the system GStreamer so it stays loadable
    cat > meson.build <<'EOF'
project('gst-webrtcdsp-aec3', 'cpp',
  version : '1.24.0',
  default_options : ['cpp_std=c++17', 'buildtype=release'])

gst_dep = dependency('gstreamer-1.0')
gstbase_dep = dependency('gstreamer-base-1.0')
gstaudio_dep = dependency('gstreamer-audio-1.0')
gstbadaudio_dep = dependency('gstreamer-bad-audio-1.0')
webrtc_dep = dependency('webrtc-audio-processing-1', version : '>= 1.0')

shared_library('gstwebrtcdsp',
  ['gstwebrtcdsp.cpp', 'gstwebrtcechoprobe.cpp', 'gstwebrtcdspplugin.cpp'],
  cpp_args : ['-DHAVE_CONFIG_H'],
  include_directories : include_directories('.'),
  dependencies : [gst_dep, gstbase_dep, gstaudio_dep, gstbadaudio_dep, webrtc_dep],
  name_prefix : 'lib',
  install : false)
EOF
}

# Compile the plugin
build_plugin() {
    echo "Building webrtcdsp plugin..."
    cd "$WORK_DIR/webrtcdsp"
    export PKG_CONFIG_PATH="/usr/local/lib/$(gcc -dumpmachine)/pkgconfig:$PKG_CONFIG_PATH"
    if [ ! -d build ]; then
        run_quiet meson setup build
    fi
    run_quiet ninja -C build
}

# Install beside the system plugins, GST_PLUGIN_PATH makes this one win
install_plugin() {
    echo "Installing plugin to $PLUGIN_DIR..."
    run_quiet sudo mkdir -p "$PLUGIN_DIR"
    run_quiet sudo cp build/libgstwebrtcdsp.so "$PLUGIN_DIR/"
}

# Confirm GStreamer picks the new plugin over the distro one
verify_plugin() {
    echo "Verifying..."
    FOUND=$(GST_PLUGIN_PATH="$PLUGIN_DIR" gst-inspect-1.0 webrtcdsp | awk '/^  Version/ {print $2}')
    if [ "$FOUND" != "$PLUGIN_TAG" ]; then
        echo "Plugin version is $FOUND, expected $PLUGIN_TAG" >&2
        exit 1
    fi
    echo "AEC3 webrtcdsp $FOUND installed, teleport.service sets GST_PLUGIN_PATH to use it"
}

# Run a step with its output in the log, show the tail only when it fails
run_quiet() {
    if ! "$@" >> "$LOG_FILE" 2>&1; then
        echo "Failed: $*" >&2
        tail -n "$LOG_TAIL_LINES" "$LOG_FILE" >&2
        exit 1
    fi
}

# Run install
main "$@"
