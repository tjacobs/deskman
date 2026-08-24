#!/usr/bin/env bash
# Starts talk.py or Deskman robot.

# Stop on errors
set -euo pipefail

# Paths
SPEAK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_SCRIPT="${SPEAK_DIR}/talk.py"
TALK_PYTHON="${SPEAK_DIR}/.venv/bin/python"
ROBOT_BIN=""
ROBOT_CANDIDATES=(
    "${SPEAK_DIR}/../robot/build/robot"
)
LOG_FILE="${SPEAK_DIR}/log.txt"
DISPLAY_DEFAULT=":0"

# Pick the first built Deskman robot binary
pick_robot_bin() {
    for candidate in "${ROBOT_CANDIDATES[@]}"; do
        if [[ -x "${candidate}" ]]; then
            ROBOT_BIN="${candidate}"
            return 0
        fi
    done
    return 1
}

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

    # Run Deskman robot, it starts talk.py itself
    if pick_robot_bin; then
        echo "Starting Deskman robot ${ROBOT_BIN}..."
        export DISPLAY="${DISPLAY:-${DISPLAY_DEFAULT}}"
        cd "$(dirname "${ROBOT_BIN}")"
        exec "${ROBOT_BIN}"
    fi

    # Start talk.py
    echo "Starting talk.py..."
    cd "${SPEAK_DIR}"
    exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}" --no-replay-robot
}

# Run service
main
