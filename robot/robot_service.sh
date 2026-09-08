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
DESKTOP_WAIT_TRIES=40
DESKTOP_WAIT_SECONDS=0.25

# Main
main() {
    # Run the robot binary when it exists, it starts talk.py itself
    if [[ -x "${ROBOT_BIN}" ]]; then
        echo "Starting Deskman robot ${ROBOT_BIN}..."
        export DISPLAY="${DISPLAY:-${DISPLAY_DEFAULT}}"
        wait_for_desktop
        cd "$(dirname "${ROBOT_BIN}")"
        exec "${ROBOT_BIN}"
    fi

    # Start talk.py without the face
    echo "Starting talk.py..."
    cd "${TALK_DIR}"
    exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}" --no-replay-robot
}

# Wait until gnome-shell owns the display, else it resets rotation after the face
wait_for_desktop() {
    export DISPLAY="${DISPLAY:-${DISPLAY_DEFAULT}}"
    local try_index
    for ((try_index = 0; try_index < DESKTOP_WAIT_TRIES; try_index++)); do
        if pgrep -x gnome-shell >/dev/null 2>&1 && xrandr --query >/dev/null 2>&1; then
            return
        fi
        sleep "${DESKTOP_WAIT_SECONDS}"
    done
}

# Run service
main
