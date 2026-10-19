#!/usr/bin/env bash
# Start robot.service from the desktop icon.

# Exit on error
set -euo pipefail

# Service the icon starts
SERVICE=robot.service

# Main
main() {
    parse_args "$@"
    start_service
}

# Read the command line flags
parse_args() {
    for argument in "$@"; do
        case "${argument}" in
            -h|--help) print_usage; exit 0 ;;
            *) echo "Unknown argument: ${argument}"; print_usage; exit 1 ;;
        esac
    done
}

# Print usage help
print_usage() {
    echo 'Usage: ./start_robot.sh'
    echo '  Start robot.service.'
}

# Start the service, and the sudoers rule allows this one without a password
start_service() {
    echo "Starting ${SERVICE}"
    sudo -n /usr/bin/systemctl start "${SERVICE}"
}

# Main
main "$@"
