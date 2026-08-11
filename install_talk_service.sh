#!/usr/bin/env bash
# Install the service that runs talk_service.sh, real.py and talk.py.
# Usage: ./install_talk_service.sh             (install, enable)
#        ./install_talk_service.sh --start     (install, enable, start now)
#        ./install_talk_service.sh --uninstall (uninstall)

# Stop on errors
set -euo pipefail

# Service config
SERVICE_NAME="talk"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Paths
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_SCRIPT="${PROJECT_DIR}/talk_service.sh"
TALK_SCRIPT="${PROJECT_DIR}/talk.py"
PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"

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
    REAL_SCRIPT="${RUN_HOME}/robot/src/real.py"

    # Verify launcher, talk, venv, and real.py exist
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
    if [[ ! -f "${REAL_SCRIPT}" ]]; then
        echo "Error: ${REAL_SCRIPT} not found" >&2
        exit 1
    fi

    # Show what will be installed
    echo "Installing ${SERVICE_NAME}.service"
    echo "  user:     ${RUN_USER}"
    echo "  launcher: ${LAUNCHER_SCRIPT}"
    echo "  real:     ${REAL_SCRIPT}"
    echo "  talk:     ${TALK_SCRIPT}"

    # Write systemd unit file
    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Talk wake-word assistant with real.py
After=network.target sound.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment=HOME=${RUN_HOME}
Environment=XDG_RUNTIME_DIR=/run/user/${RUN_UID}
ExecStart=${LAUNCHER_SCRIPT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF

    # Set permissions
    chmod 644 "${SERVICE_FILE}"
    chmod 755 "${LAUNCHER_SCRIPT}" "${TALK_SCRIPT}"

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
