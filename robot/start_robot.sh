#!/usr/bin/env bash
# Start robot.service from the desktop icon.

# Exit on error
set -euo pipefail

# Service the icon starts
SERVICE=robot.service

# Main
main() {
    # Start the service, and the sudoers rule allows this one without a password
    echo "Starting ${SERVICE}"
    sudo -n /usr/bin/systemctl start "${SERVICE}"
}

# Main
main
