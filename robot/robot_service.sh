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

# Main
main() {
    # Give talk.py --cloud the key when the service did not inherit bashrc
    load_openai_key

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
