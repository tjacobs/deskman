#!/usr/bin/env bash
# Restart robot and teleport after a short delay, so the caller can finish speaking.

set -euo pipefail

# Installed copy that the sudoers rule allows without a password
INSTALLED_SCRIPT="/usr/local/bin/deskman-restart-services"

# Hand off to the installed copy when not root, so no password is needed
if [[ "${EUID}" -ne 0 ]]; then
    exec sudo -n "${INSTALLED_SCRIPT}"
fi

# Restart both services in a moment
systemd-run --quiet --collect --on-active=1s /bin/systemctl restart robot.service teleport.service
