#!/usr/bin/env bash
# Install robot, talk, and teleport, then the robot and teleport services and system setup.

# Stop on errors
set -euo pipefail

# Repo root, so the script works from any directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Main
main() {
    # Install robot libraries
    "${ROOT_DIR}/robot/install.sh"

    # Install talk, listen and the local model are on by default
    "${ROOT_DIR}/talk/install.sh"

    # Install teleport libraries
    "${ROOT_DIR}/teleport/install.sh"

    # Install and enable the robot service
    "${ROOT_DIR}/robot/install_robot_service.sh"

    # Install and enable the teleport service
    "${ROOT_DIR}/teleport/install_teleport_service.sh"

    # Configure boot, login, and the desktop
    "${ROOT_DIR}/robot/install_system.sh"
}

# Run install
main "$@"
