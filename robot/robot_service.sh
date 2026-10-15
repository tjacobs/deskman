#!/usr/bin/env bash
# Starts the Deskman robot, which starts talk.py.

# Stop on errors
set -euo pipefail

# Paths
ROBOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_DIR="$(cd "${ROBOT_DIR}/../talk" && pwd)"
ROBOT_BIN="${ROBOT_DIR}/build/robot"
TALK_SCRIPT="${TALK_DIR}/talk.py"
TALK_PYTHON="${TALK_DIR}/.venv/bin/python"
DISPLAY_DEFAULT=":0"
OPENAI_ENV_FILE="${TALK_DIR}/openai.env"

# How long to wait for the window manager at boot, and how often to look
WINDOW_MANAGER_WAIT_SECONDS=60
WINDOW_MANAGER_POLL_SECONDS=1

# Socket a Wayland compositor makes when it is up, as on the Pi
WAYLAND_SOCKET_NAME="wayland-0"

# Main
main() {
    # Give talk.py --cloud the key when the service did not inherit bashrc
    load_openai_key

    # Run the robot binary when it exists, it starts talk.py itself
    if [[ -x "${ROBOT_BIN}" ]]; then
        echo "Starting Deskman robot ${ROBOT_BIN}..."
        export DISPLAY="${DISPLAY:-${DISPLAY_DEFAULT}}"
        wait_for_window_manager
        cd "$(dirname "${ROBOT_BIN}")"
        exec "${ROBOT_BIN}"
    fi

    # Start talk.py without the face
    echo "Starting talk.py..."
    cd "${TALK_DIR}"
    exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}"
}

# Wait for the window manager, a face window made before it is always on top and hides the call video
wait_for_window_manager() {
    local waited=0
    while ! window_manager_ready; do
        if (( waited >= WINDOW_MANAGER_WAIT_SECONDS )); then
            echo "No window manager after ${WINDOW_MANAGER_WAIT_SECONDS} seconds, starting the robot anyway."
            return
        fi
        sleep "${WINDOW_MANAGER_POLL_SECONDS}"
        waited=$((waited + WINDOW_MANAGER_POLL_SECONDS))
    done
}

# Check for a Wayland compositor, as on the Pi, or an X window manager, as on the Jetson
window_manager_ready() {
    # A Wayland compositor shows itself with a socket
    local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    if [[ -S "${runtime_dir}/${WAYLAND_SOCKET_NAME}" ]]; then
        return 0
    fi

    # An X window manager sets a check property on the root window
    xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q "window id"
}

# Load OPENAI_API_KEY from the user env file when unset
load_openai_key() {
    if [[ -n "${OPENAI_API_KEY:-}" ]]; then
        return
    fi
    if [[ ! -f "${OPENAI_ENV_FILE}" ]]; then
        return
    fi
    set -a
    . "${OPENAI_ENV_FILE}"
    set +a
}

# Run service
main
