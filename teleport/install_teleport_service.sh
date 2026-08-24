#!/usr/bin/env bash
# Install the service that runs teleport.
# Usage: ./install_teleport_service.sh             (install, enable)
#        ./install_teleport_service.sh --start     (install, enable, start now)
#        ./install_teleport_service.sh --uninstall (uninstall)

# Stop on errors
set -euo pipefail

# Service config
SERVICE_NAME="teleport"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PLUGIN_PATH="/usr/local/lib/deskman-gstreamer-1.0"

# Paths
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPORT_BIN="${PROJECT_DIR}/build/teleport"

# Print usage help
print_usage() {
    echo "Usage: ./install_teleport_service.sh [--start|--uninstall]"
    echo "  (no arg)     install and enable teleport.service"
    echo "  --start      install, enable, and start now"
    echo "  --uninstall  stop, disable, and remove teleport.service"
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

    # Verify the binary exists
    if [[ ! -x "${TELEPORT_BIN}" ]]; then
        echo "Error: ${TELEPORT_BIN} not found. Build with cmake and make first." >&2
        exit 1
    fi

    # Show what will be installed
    echo "Installing ${SERVICE_NAME}.service"
    echo "  user:   ${RUN_USER}"
    echo "  binary: ${TELEPORT_BIN}"

    # Write systemd unit file
    cat > "${SERVICE_FILE}" <<EOF
# Programs connect to \$XDG_RUNTIME_DIR/teleport.interface.

[Unit]
Description=Teleport
After=display-manager.service network.target graphical.target
Wants=graphical.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_DIR}/build
Environment=HOME=${RUN_HOME}
Environment=DISPLAY=:0
Environment=XDG_RUNTIME_DIR=/run/user/${RUN_UID}
Environment=GST_PLUGIN_PATH=${PLUGIN_PATH}
Environment=LIBCAMERA_LOG_LEVELS=*:ERROR
ExecStart=${TELEPORT_BIN}
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
StandardOutput=journal
StandardError=journal
KillMode=mixed

[Install]
WantedBy=graphical.target
EOF

    # Set permissions
    chmod 644 "${SERVICE_FILE}"
    chmod 755 "${TELEPORT_BIN}"

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
