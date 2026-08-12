#!/usr/bin/env bash
# Block or restore internet access for offline testing.
# Usage: ./tools/offline.sh
#        ./tools/offline.sh --fix

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config
TABLE_NAME="speak_offline"
CHAIN_NAME="out"
STATE_FILE="/var/run/speak-offline"
PUBLIC_TEST_HOST="1.1.1.1"
LOCAL_TEST_HOST="192.168.1.254"
PING_COUNT=1
PING_WAIT_SECONDS=2

# Re-exec with sudo if not root, help does not need it
if [[ "${EUID}" -ne 0 && "${1:-}" != "-h" && "${1:-}" != "--help" ]]; then
    exec sudo --preserve-env=SUDO_USER,SSH_CONNECTION bash "${BASH_SOURCE[0]}" "$@"
fi

# Main
main() {
    # Parse command line arguments
    parse_args "$@"

    # Block or restore internet access
    if [[ "${FIX_MODE}" == "true" ]]; then
        go_online
    else
        go_offline
    fi
}

# Parse command line arguments
parse_args() {
    FIX_MODE="false"

    # Read each argument
    for argument in "$@"; do
        if [[ "${argument}" == "--fix" ]]; then
            FIX_MODE="true"
        elif [[ "${argument}" == "-h" || "${argument}" == "--help" ]]; then
            print_usage
            exit 0
        else
            echo "Unknown argument: ${argument}"
            print_usage
            exit 1
        fi
    done
}

# Print usage help
print_usage() {
    echo "Usage: ./tools/offline.sh [--fix]"
    echo "  --fix     restore internet access"
    echo "  (no arg)  block outbound internet, keeping SSH and local networks"
}

# Block new outbound internet, keep SSH and local networks working
go_offline() {
    # Remove any old rules
    nft delete table inet "${TABLE_NAME}" 2>/dev/null || true
    nft delete table inet robot_offline 2>/dev/null || true

    # Create output filter chain
    nft add table inet "${TABLE_NAME}"
    nft add chain inet "${TABLE_NAME}" "${CHAIN_NAME}" '{ type filter hook output priority 0; policy accept; }'

    # Allow loopback
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" oif "lo" accept

    # Allow traffic to the current SSH client
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        ssh_client_ip="${SSH_CONNECTION%% *}"
        if [[ "${ssh_client_ip}" == *:* ]]; then
            nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip6 daddr "${ssh_client_ip}" accept
        else
            nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip daddr "${ssh_client_ip}" accept
        fi
        echo "Keeping SSH open to ${ssh_client_ip}"
    fi

    # Allow local networks
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip daddr 127.0.0.0/8 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip daddr 10.0.0.0/8 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip daddr 172.16.0.0/12 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip daddr 192.168.0.0/16 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip daddr 169.254.0.0/16 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip6 daddr ::1 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip6 daddr fc00::/7 accept
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" ip6 daddr fe80::/10 accept

    # Drop all other outbound traffic
    nft add rule inet "${TABLE_NAME}" "${CHAIN_NAME}" drop

    # Mark offline mode active
    date --iso-8601=seconds > "${STATE_FILE}"
    echo "Internet access blocked."
    verify_offline
}

# Restore normal internet access
go_online() {
    # Remove offline firewall rules
    nft delete table inet "${TABLE_NAME}" 2>/dev/null || true
    nft delete table inet robot_offline 2>/dev/null || true
    rm -f "${STATE_FILE}" /var/run/robot-offline

    # Report result
    if [[ -f "${STATE_FILE}" ]]; then
        echo "Failed to restore internet access."
        exit 1
    fi

    echo "Internet access restored."
    verify_online
}

# Confirm public internet is blocked
verify_offline() {
    # Check public host is unreachable
    if ping -c "${PING_COUNT}" -W "${PING_WAIT_SECONDS}" "${PUBLIC_TEST_HOST}" >/dev/null 2>&1; then
        echo "Warning: ${PUBLIC_TEST_HOST} is still reachable."
        exit 1
    fi

    # Check local gateway is still reachable
    if ! ping -c "${PING_COUNT}" -W "${PING_WAIT_SECONDS}" "${LOCAL_TEST_HOST}" >/dev/null 2>&1; then
        echo "Warning: local host ${LOCAL_TEST_HOST} is not reachable."
    fi

    echo "Offline mode active. Run ./tools/offline.sh --fix to restore internet."
}

# Confirm public internet works again
verify_online() {
    # Check public host is reachable
    if ! ping -c "${PING_COUNT}" -W "${PING_WAIT_SECONDS}" "${PUBLIC_TEST_HOST}" >/dev/null 2>&1; then
        echo "Warning: ${PUBLIC_TEST_HOST} is not reachable yet."
        exit 1
    fi

    echo "Online mode active."
}

# Main
main "$@"
