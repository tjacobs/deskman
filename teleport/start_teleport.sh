#!/bin/sh

# Start teleport as the user who owns the binary

# Path to the built teleport binary
TELEPORT_BINARY_GLOB="/home/*/speak/teleport/build/teleport"

# Default display to use
DEFAULT_DISPLAY=":0"

# Default plugin path to use
DEFAULT_PLUGIN_PATH="/usr/local/lib/deskman-gstreamer-1.0"

# Main
main() {
    # Fail on error
    set -e

    # Find the built binary and run it as its owner
    binary=$(find_teleport_binary)
    start_teleport "$binary" "$@"
}

# Locate the first built teleport under a user home
find_teleport_binary() {
    # Walk home directories for the robot binary
    for path in $TELEPORT_BINARY_GLOB; do
        if [ -x "$path" ]; then
            echo "$path"
            return 0
        fi
    done

    # Stop if this machine has no build
    echo "Teleport binary not found in $TELEPORT_BINARY_GLOB" >&2
    exit 1
}

# Drop root and exec teleport with that user's runtime dir
start_teleport() {
    binary=$1
    shift

    # Read the owner of the binary
    user=$(stat -c %U "$binary")
    user_id=$(id -u "$user")
    home=$(getent passwd "$user" | cut -d: -f6)

    # Match the session Deskman already uses
    export HOME=$home
    export USER=$user
    export LOGNAME=$user
    export DISPLAY=${DISPLAY:-$DEFAULT_DISPLAY}
    export XDG_RUNTIME_DIR=/run/user/$user_id
    export GST_PLUGIN_PATH=${GST_PLUGIN_PATH:-$DEFAULT_PLUGIN_PATH}
    export LIBCAMERA_LOG_LEVELS=${LIBCAMERA_LOG_LEVELS:-*:ERROR}

    # Run from the build directory as the binary owner, no leftover wrapper process
    cd "$(dirname "$binary")"
    group_id=$(id -g "$user")
    exec setpriv --reuid="$user_id" --regid="$group_id" --init-groups -- "$binary" "$@"
}

# Main
main "$@"
