#!/usr/bin/env bash
# Starts talk.py

# Stop on errors
set -euo pipefail

# Paths
SPEAK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_SCRIPT="${SPEAK_DIR}/talk.py"
TALK_PYTHON="${SPEAK_DIR}/.venv/bin/python"
LOG_FILE="${SPEAK_DIR}/log.txt"

# Main
main() {
    # Tell the user where output goes, then append to the log
    mkdir -p "$(dirname "${LOG_FILE}")"
    echo "Writing log.txt"
    echo "Run: tail -f log.txt"
    exec >> "${LOG_FILE}" 2>&1
    echo ""
    echo "=== robot_service $(date -Is) ==="

    # Quit if talk.py or its venv is missing
    if [[ ! -f "${TALK_SCRIPT}" || ! -x "${TALK_PYTHON}" ]]; then
        echo "Error: talk.py or .venv missing in ${SPEAK_DIR}" >&2
        exit 1
    fi

    # Start talk
    echo "Starting talk.py..."
    cd "${SPEAK_DIR}"
    exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}" --no-replay-robot
}

# Run service
main
