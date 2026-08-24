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
LOG_FILE="${ROBOT_DIR}/log.txt"
DISPLAY_DEFAULT=":0"

# Main
main() {
    # Tell the user where output goes
    mkdir -p "$(dirname "${LOG_FILE}")"
    echo "Writing ${LOG_FILE}"
    echo "Run: tail -f ${LOG_FILE}"

    # Append all to log
    exec >> "${LOG_FILE}" 2>&1
    echo ""
    echo "=== robot_service $(date -Is) ==="

    # Run the robot binary when it exists, it starts talk.py itself
    if [[ -x "${ROBOT_BIN}" ]]; then
        echo "Starting Deskman robot ${ROBOT_BIN}..."
        export DISPLAY="${DISPLAY:-${DISPLAY_DEFAULT}}"
        cd "$(dirname "${ROBOT_BIN}")"
        exec "${ROBOT_BIN}"
    fi

    # Start talk.py without the face
    echo "Starting talk.py..."
    cd "${TALK_DIR}"
    exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}" --no-replay-robot
}

# Run service
main
