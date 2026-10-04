#!/usr/bin/env bash
# Start robot.service from the desktop icon, offering the OpenAI key setup when no key is saved.

set -euo pipefail

# Repo folders, this script sits in robot/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

# Where talk reads the key from, and the script that puts one there
KEY_FILE="${REPO_DIR}/talk/openai.env"
KEY_NAME=OPENAI_API_KEY
KEY_SCRIPT="${REPO_DIR}/talk/install_openai_key.sh"
KEY_PYTHON="${REPO_DIR}/talk/.venv/bin/python"

# Wording of the question that offers the setup
KEY_TITLE='OpenAI key'
KEY_QUESTION=$'No OpenAI key is saved, so the robot talks with the local model only.\n\nSet a key up now?'
KEY_OK_LABEL='Set up key'
KEY_SKIP_LABEL='Skip'

# Terminals tried in order, the setup runs in one so its messages are on screen
TERMINALS=(x-terminal-emulator lxterminal gnome-terminal xterm)

# Unit the icon starts
SERVICE=robot.service

# Main
main() {
    parse_args "$@"

    # Deal with the key first, the robot face covers the screen once it is up
    if [[ "${SKIP_KEY}" == no ]]; then
        offer_key_setup
    fi

    # Hand the rest over to systemd
    start_service
}

# Read the command line flags
parse_args() {
    SKIP_KEY=no
    for argument in "$@"; do
        case "${argument}" in
            --no-key) SKIP_KEY=yes ;;
            -h|--help) print_usage; exit 0 ;;
            *) echo "Unknown argument: ${argument}"; print_usage; exit 1 ;;
        esac
    done
}

# Print usage help
print_usage() {
    echo 'Usage: ./start_robot.sh [--no-key]'
    echo '  --no-key    start the service without asking about an OpenAI key'
    echo '  (no arg)    offer the key setup when no key is saved, then start the service'
}

# Ask about a key when none is saved, a skip just carries on
offer_key_setup() {
    # Nothing to offer when talk already has a key
    if have_key; then
        return
    fi

    # Nothing to show without a screen to put it on, or without the setup script
    if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" || ! -f "${KEY_SCRIPT}" ]]; then
        return
    fi

    # Let them say no, a key only buys the cloud voices
    if ! ask_about_key; then
        echo 'Starting without a key, the local model answers.'
        return
    fi

    # Open the key page and save whatever gets pasted
    run_key_setup
}

# True when talk has a key to read
have_key() {
    if [[ -n "${!KEY_NAME:-}" ]]; then
        return 0
    fi
    grep -q "^${KEY_NAME}=." "${KEY_FILE}" 2>/dev/null
}

# Ask whether to set a key up now
ask_about_key() {
    # Go straight to the setup when there is no zenity to ask with
    if ! command -v zenity >/dev/null; then
        return 0
    fi
    zenity --question --title "${KEY_TITLE}" --text "${KEY_QUESTION}" --ok-label "${KEY_OK_LABEL}" --cancel-label "${KEY_SKIP_LABEL}"
}

# Run the key setup where its messages can be read
run_key_setup() {
    # The setup is Python, so use the talk venv when it is built
    python_path="${KEY_PYTHON}"
    if [[ ! -x "${python_path}" ]]; then
        python_path="$(command -v python3)"
    fi

    # A terminal window shows the checking and saving messages, a cancel or a bad key is not fatal
    terminal="$(find_terminal)"
    if [[ -z "${terminal}" ]]; then
        "${python_path}" "${KEY_SCRIPT}" || true
        return
    fi
    "${terminal}" -e "${python_path} ${KEY_SCRIPT}" || true
}

# First terminal emulator installed, empty when none are
find_terminal() {
    for terminal in "${TERMINALS[@]}"; do
        if command -v "${terminal}" >/dev/null; then
            command -v "${terminal}"
            return
        fi
    done
}

# Start the unit, the sudoers rule allows this one without a password
start_service() {
    echo "Starting ${SERVICE}"
    sudo -n /usr/bin/systemctl start "${SERVICE}"
}

# Main
main "$@"
