#!/usr/bin/env bash
# Install the service that runs robot_service.sh.
# Usage: ./robot_service_install.sh             (install, enable)
#        ./robot_service_install.sh --start     (install, enable, start now)
#        ./robot_service_install.sh --uninstall (uninstall)

# Stop on errors
set -euo pipefail

# Service config
SERVICE_NAME="robot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Paths
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_SCRIPT="${PROJECT_DIR}/robot_service.sh"
TALK_SCRIPT="${PROJECT_DIR}/talk.py"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"

# Print usage help
print_usage() {
    echo "Usage: ./robot_service_install.sh [--start|--uninstall]"
    echo "  (no arg)     install and enable robot.service"
    echo "  --start      install, enable, and start now"
    echo "  --uninstall  stop, disable, and remove robot.service"
}

# Help does not need root
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    print_usage
    exit 0
fi

# Reject unknown arguments
if [[ "${1:-}" != "" && "${1:-}" != "--start" && "${1:-}" != "--uninstall" ]]; then
    echo "Unknown argument: ${1}"
    print_usage
    exit 1
fi

# Sudo
if [[ "${EUID}" -ne 0 ]]; then
    exec sudo --preserve-env=SUDO_USER,HOME bash "${BASH_SOURCE[0]}" "$@"
fi

# Main
main() {
    # Resolve paths for the install user
    RUN_USER="${SUDO_USER:-$(id -un)}"
    RUN_GROUP="$(id -gn "${RUN_USER}")"
    RUN_UID="$(id -u "${RUN_USER}")"
    RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6)"

    # Verify launcher, talk, and venv exist
    if [[ ! -f "${LAUNCHER_SCRIPT}" ]]; then
        echo "Error: ${LAUNCHER_SCRIPT} not found" >&2
        exit 1
    fi
    if [[ ! -f "${TALK_SCRIPT}" ]]; then
        echo "Error: ${TALK_SCRIPT} not found" >&2
        exit 1
    fi
    if [[ ! -x "${PYTHON_BIN}" ]]; then
        echo "Error: ${PYTHON_BIN} not found. Run ./install.sh --listen --talk first." >&2
        exit 1
    fi

    # Show what will be installed
    echo "Installing ${SERVICE_NAME}.service"
    echo "  user:     ${RUN_USER}"
    echo "  launcher: ${LAUNCHER_SCRIPT}"
    echo "  talk:     ${TALK_SCRIPT}"

    # Write systemd unit file
    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Robot wake-word assistant
After=network.target sound.target graphical.target
Wants=graphical.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=HOME=${RUN_HOME}
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/${RUN_UID}
ExecStart=${LAUNCHER_SCRIPT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
KillMode=mixed

[Install]
WantedBy=graphical.target
EOF

    # Set permissions
    chmod 644 "${SERVICE_FILE}"
    chmod 755 "${LAUNCHER_SCRIPT}" "${TALK_SCRIPT}"

    # Remove leftover talk.service from the old name
    if [[ -f /etc/systemd/system/talk.service ]]; then
        echo "Removing old talk.service"
        systemctl stop talk.service 2>/dev/null || true
        systemctl disable talk.service 2>/dev/null || true
        rm -f /etc/systemd/system/talk.service
    fi

    # Enable on boot
    echo "Reloading systemd"
    systemctl daemon-reload
    echo "Enabling ${SERVICE_NAME}.service to start on boot"
    systemctl enable "${SERVICE_NAME}.service"

    # Start now when requested
    if [[ "${1:-}" == "--start" ]]; then
        echo "Starting ${SERVICE_NAME}.service"
        systemctl restart "${SERVICE_NAME}.service"
    fi

    # Done
    echo ""
    echo "Installed. Useful commands:"
    echo "  sudo service ${SERVICE_NAME} start"
    echo "  sudo service ${SERVICE_NAME} stop"
    echo "  sudo service ${SERVICE_NAME} status"
    echo "  journalctl -u ${SERVICE_NAME} -f"
}

# Uninstall
uninstall() {
    # Stop and disable service
    echo "Stopping and disabling ${SERVICE_NAME}.service"
    systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true

    # Remove file
    echo "Removing ${SERVICE_FILE}"
    rm -f "${SERVICE_FILE}"

    # Reload systemd
    systemctl daemon-reload

    # Done
    echo "Uninstalled ${SERVICE_NAME}.service"
}

# Run install or uninstall
if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
else
    main "$@"
fi
