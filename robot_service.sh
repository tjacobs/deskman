#!/usr/bin/env bash
# Starts talk.py or Deskman robot.

# Stop on errors
set -euo pipefail

# Paths
SPEAK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_SCRIPT="${SPEAK_DIR}/talk.py"
TALK_PYTHON="${SPEAK_DIR}/.venv/bin/python"
ROBOT_BIN="${HOME}/Deskman/src/build/robot"
ROBOT_DIR="$(dirname "${ROBOT_BIN}")"
LOG_FILE="${SPEAK_DIR}/log.txt"
DISPLAY_DEFAULT=":0"

# Main
main() {
    # Tell the user where output goes
    mkdir -p "$(dirname "${LOG_FILE}")"
    echo "Writing log.txt"
    echo "Run: tail -f log.txt"

    # Append all to log
    exec >> "${LOG_FILE}" 2>&1
    echo ""
    echo "=== robot_service $(date -Is) ==="

    # Run Deskman robot, it starts talk.py itself
    if [[ -x "${ROBOT_BIN}" ]]; then
        echo "Starting Deskman robot..."
        export DISPLAY="${DISPLAY:-${DISPLAY_DEFAULT}}"
        cd "${ROBOT_DIR}"
        exec "${ROBOT_BIN}"
    fi

    # Start talk.py
    echo "Starting talk.py..."
    cd "${SPEAK_DIR}"
    exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}" --no-replay-robot
}

# Run service
main
