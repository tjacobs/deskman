#!/usr/bin/env bash
# Starts real.py, then talk.py

# Stop on errors
set -euo pipefail
set +m

# Paths
SPEAK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_SRC="${HOME}/robot/src"
REAL_SCRIPT="${ROBOT_SRC}/real.py"
TALK_SCRIPT="${SPEAK_DIR}/talk.py"
ROBOT_PYTHON="$(command -v python)"
TALK_PYTHON="${SPEAK_DIR}/.venv/bin/python"
LOG_FILE="${HOME}/speak/log.txt"
STOP_WAIT_SECONDS=5

# Process IDs
real_pid=""
talk_pid=""
stopping=false

# Main
main() {
    # Append all output to log file
    mkdir -p "$(dirname "${LOG_FILE}")"
    exec >> "${LOG_FILE}" 2>&1
    echo ""
    echo "=== talk_service $(date -Is) ==="

    # Quit when real.py is missing
    if [[ ! -f "${REAL_SCRIPT}" ]]; then
        echo "Error: ${REAL_SCRIPT} not found" >&2
        exit 1
    fi

    # Quit when talk.py or its venv is missing
    if [[ ! -f "${TALK_SCRIPT}" || ! -x "${TALK_PYTHON}" ]]; then
        echo "Error: talk.py or .venv missing in ${SPEAK_DIR}" >&2
        exit 1
    fi

    # Start real.py
    echo "Starting real.py..."
    (cd "${ROBOT_SRC}" && exec "${ROBOT_PYTHON}" -u "${REAL_SCRIPT}") &
    real_pid=$!

    # Start talk wake-word loop
    echo "Starting talk.py..."
    (cd "${SPEAK_DIR}" && exec "${TALK_PYTHON}" -u "${TALK_SCRIPT}") &
    talk_pid=$!

    # Wait until either exits
    while kill -0 "${real_pid}" 2>/dev/null && kill -0 "${talk_pid}" 2>/dev/null; do
        sleep 0.2
    done

    # Stop
    stop_processes
}

# Stop talk and real politely
stop_processes() {
    # Check
    if [[ "${stopping}" == true ]]; then
        exit 0
    fi
    stopping=true
    trap - INT TERM

    # Stop talk, then real so motors can sit down
    stop_pid "${talk_pid}"
    stop_pid "${real_pid}"

    # Stop any remaining descendants of this script
    local child_pid
    for child_pid in $(pgrep -P $$ 2>/dev/null || true); do
        stop_pid "${child_pid}"
    done

    # Done
    echo "All done."
    exit 0
}

# Stop one process with TERM, then KILL if needed
stop_pid() {
    local pid="$1"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        return
    fi

    # Ask to stop, wait for a clean exit
    kill -TERM "${pid}" 2>/dev/null || true
    local waited=0
    local wait_steps=$((STOP_WAIT_SECONDS * 10))
    while kill -0 "${pid}" 2>/dev/null && (( waited < wait_steps )); do
        sleep 0.1
        waited=$((waited + 1))
    done

    # Force only if still running, then wait until it is gone
    if kill -0 "${pid}" 2>/dev/null; then
        kill -KILL "${pid}" 2>/dev/null || true
        while kill -0 "${pid}" 2>/dev/null; do
            sleep 0.1
        done
    fi
}

# Stop on interrupt or termination
trap stop_processes INT TERM

# Run service
main
