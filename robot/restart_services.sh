#!/usr/bin/env bash
# Restart robot and teleport after a short delay, so the caller can finish speaking.

set -euo pipefail

systemd-run --quiet --collect --on-active=1s /bin/systemctl restart robot.service teleport.service
