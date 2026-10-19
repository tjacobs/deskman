#!/usr/bin/env bash
# Delete the owner's accounts, data, and identity so the robot can ship to a customer.

# Stop on errors
set -euo pipefail

# Repo root, so the script works from any directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TALK_DIR="${ROOT_DIR}/talk"
ROBOT_DIR="${ROOT_DIR}/robot"
TELEPORT_DIR="${ROOT_DIR}/teleport"

# Flag values, kept here so the arg defaults are not in the code
FLAG_ON=1
FLAG_OFF=0

# Word that must be typed to start the delete
CONFIRM_WORD="DELETE"

# Character that makes the rows above and below a banner message
BANNER_CHARACTER="*"

# Tools and files the delete touches
GITHUB_LOGIN_FILE="${HOME}/.config/gh/hosts.yml"
GITHUB_HOST="github.com"
AUTHORIZED_KEYS_FILE="${HOME}/.ssh/authorized_keys"
SHELL_HISTORY_FILE="${HOME}/.bash_history"
TELEPORT_CONFIG_FILE="${TELEPORT_DIR}/config.json"
AVAHI_CONFIG_FILE="/etc/avahi/avahi-daemon.conf"

# Teleport settings that stay, the rest is made again on the next start
TELEPORT_KEPT_SETTINGS="autoAnswer phone"

# Rewrites the teleport config with only the kept settings
KEEP_SETTINGS_PROGRAM='import json, sys; path = sys.argv[1]; config = json.load(open(path)); json.dump({name: config[name] for name in sys.argv[2:] if name in config}, open(path, "w"), indent=2)'
WIRELESS_CONNECTION_TYPE="802-11-wireless"
HOSTNAME_PATTERN='^[a-z]+[0-9]+$'

# Main
main() {
    # Read the options
    parse_args "$@"

    # Match globs that find nothing to nothing, so empty folders are fine
    shopt -s nullglob

    # Show what would happen first
    DRY_RUN="${FLAG_ON}"
    print_banner "This robot will be reset:"
    delete_robot

    # Stop here when only a preview was asked for
    if [[ "${PREVIEW_ONLY}" == "${FLAG_ON}" ]]; then
        print_reminders
        exit 0
    fi

    # Ask before anything is deleted
    confirm_delete

    # Do the delete
    DRY_RUN="${FLAG_OFF}"
    print_banner "Resetting the robot:"
    delete_robot

    # Say what is left for a person to do
    print_reminders
}

# Read the command line options
parse_args() {
    # Start with the safe choices, everything is deleted and a prompt is shown
    PREVIEW_ONLY="${FLAG_OFF}"
    ASSUME_YES="${FLAG_OFF}"
    KEEP_OPENAI_KEY="${FLAG_OFF}"
    DELETE_SSH_KEY="${FLAG_OFF}"
    DELETE_WIFI="${FLAG_OFF}"
    NEW_HOSTNAME=""

    # Read each option
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                PREVIEW_ONLY="${FLAG_ON}"
                ;;
            --yes)
                ASSUME_YES="${FLAG_ON}"
                ;;
            --keep-key)
                KEEP_OPENAI_KEY="${FLAG_ON}"
                ;;
            --delete-ssh-key)
                DELETE_SSH_KEY="${FLAG_ON}"
                ;;
            --delete-wifi)
                DELETE_WIFI="${FLAG_ON}"
                ;;
            --hostname)
                NEW_HOSTNAME="${2:-}"
                shift
                ;;
            --help)
                print_usage
                exit 0
                ;;
            *)
                print_usage
                exit 1
                ;;
        esac
        shift
    done

    # Refuse a hostname that has no number, as the robot takes its number from the end
    if [[ -n "${NEW_HOSTNAME}" && ! "${NEW_HOSTNAME}" =~ ${HOSTNAME_PATTERN} ]]; then
        echo "Error: hostname needs letters then a number, like deskman7."
        exit 1
    fi
}

# Show the options
print_usage() {
    echo "Usage: robot/reset_to_factory.sh [--dry-run] [--yes] [--keep-key] [--delete-ssh-key] [--delete-wifi] [--hostname name]"
    echo "  --dry-run          only show what would be deleted"
    echo "  --yes              do not ask before deleting"
    echo "  --keep-key         keep the OpenAI key in talk/openai.env"
    echo "  --delete-ssh-key   remove the authorized SSH keys, they are kept for remote support otherwise"
    echo "  --delete-wifi      forget every saved WiFi network, they are kept otherwise"
    echo "  --hostname name    rename the robot, like deskman7, so teleport gets its own number"
}

# Print a message between rows of stars as wide as the message, so it stands out above the steps
print_banner() {
    # Make a row as wide as the message
    local row
    row="$(printf '%*s' "${#1}" '' | tr ' ' "${BANNER_CHARACTER}")"

    # Print the message between two rows
    echo
    echo "${row}"
    echo "$1"
    echo "${row}"
    echo
}

# Ask for the confirm word unless told to go ahead
confirm_delete() {
    # Go ahead when asked to
    if [[ "${ASSUME_YES}" == "${FLAG_ON}" ]]; then
        return
    fi

    # Warn that a remote session will drop when the WiFi is forgotten
    if [[ -n "${SSH_CONNECTION:-}" && "${DELETE_WIFI}" == "${FLAG_ON}" ]]; then
        echo
        echo "Warning: this is an SSH session, and forgetting the WiFi will end it."
    fi

    # Ask for the word
    echo
    read -r -p "Type ${CONFIRM_WORD} to reset this robot: " answer
    if [[ "${answer}" != "${CONFIRM_WORD}" ]]; then
        echo "Cancelled, nothing was changed."
        exit 1
    fi
}

# Do every delete step, in the order that stops writers first and drops the network last
delete_robot() {
    # Start a fresh list of what is kept
    KEPT_NOTES=()

    # Do each step
    stop_services
    remove_openai_key
    remove_github_login
    remove_ssh_keys
    remove_talk_data
    remove_robot_data
    remove_teleport_identity
    rename_robot
    reload_units
    clear_logs
    remove_wifi

    # List what was kept after the steps
    print_kept_notes
}

# Stop the services so nothing rewrites the files that are deleted
stop_services() {
    act sudo systemctl stop robot.service teleport.service
}

# Delete the OpenAI key
remove_openai_key() {
    # Keep it when asked to
    if [[ "${KEEP_OPENAI_KEY}" == "${FLAG_ON}" ]]; then
        keep_note "Keeping the OpenAI key"
        return
    fi

    # Delete the key file and any renamed copies
    act rm -f -- "${TALK_DIR}"/openai.env*
}

# Sign out of GitHub and forget the owner's git name
remove_github_login() {
    # Delete the saved GitHub login, the logout command waits on a keyring dialog
    act rm -f -- "${GITHUB_LOGIN_FILE}"

    # Forget the owner's name and email, and the helper that used the login
    act git config --global --unset-all user.name
    act git config --global --unset-all user.email
    act git config --global --remove-section "credential.https://${GITHUB_HOST}"
    act git config --global --remove-section "credential.https://gist.${GITHUB_HOST}"
}

# Remove the owner's SSH keys only when asked to
remove_ssh_keys() {
    # Keep them unless asked to delete
    if [[ "${DELETE_SSH_KEY}" != "${FLAG_ON}" ]]; then
        keep_note "Keeping the authorized SSH keys"
        return
    fi

    # Empty the file so no key can log in
    act truncate -c -s 0 -- "${AUTHORIZED_KEYS_FILE}"
}

# Delete what talk saved about the owner
remove_talk_data() {
    # Conversations, memories, and reminders
    act rm -rf -- "${TALK_DIR}"/history/*
    act rm -f -- "${TALK_DIR}/memory.json" "${TALK_DIR}/reminders.json"

    # Linked calendar and speaker accounts
    act rm -f -- "${TALK_DIR}/accounts.json"

    # Test recordings of the owner's voice and talk logs
    act rm -f -- "${TALK_DIR}"/audio/test_*.wav
    act rm -f -- "${TALK_DIR}/log.txt" "${TALK_DIR}/monitor.log"
}

# Delete what the robot recorded and logged
remove_robot_data() {
    act rm -rf -- "${ROBOT_DIR}"/recordings/*
    act rm -f -- "${ROBOT_DIR}/log.txt"
}

# Drop the teleport name and robot key but keep the phone number, a new key and name are made on the next start
remove_teleport_identity() {
    # Keep only the kept settings
    if [[ -f "${TELEPORT_CONFIG_FILE}" ]]; then
        act python3 -c "${KEEP_SETTINGS_PROGRAM}" "${TELEPORT_CONFIG_FILE}" ${TELEPORT_KEPT_SETTINGS}
    fi

    # Delete the old name file and old config, both from the build folder where the service runs, and the log
    act rm -f -- "${TELEPORT_DIR}/build/device.id" "${TELEPORT_DIR}/build/config.json" "${TELEPORT_DIR}/log.txt"
}

# Give the robot its own hostname when one was asked for, and announce it
rename_robot() {
    # Set the name, and the local address that goes with it, when one was asked for
    if [[ -n "${NEW_HOSTNAME}" ]]; then
        act sudo hostnamectl set-hostname "${NEW_HOSTNAME}"
        act sudo sed -i "s/^127.0.1.1.*/127.0.1.1 ${NEW_HOSTNAME}/" /etc/hosts
    fi

    # Make Avahi announce the robot name, whether or not it was just changed
    announce_hostname
}

# Change the name Avahi announces as name.local, the installer wrote an old one into its config
announce_hostname() {
    # Find the name that should be announced
    local announced_name="${NEW_HOSTNAME:-$(hostname)}"

    # Nothing to do when there is no config, or it already has the name
    if [[ ! -f "${AVAHI_CONFIG_FILE}" ]] || grep -qx "host-name=${announced_name}" "${AVAHI_CONFIG_FILE}"; then
        return
    fi

    # Write the name and restart Avahi
    act sudo sed -i "s/^#*host-name=.*/host-name=${announced_name}/" "${AVAHI_CONFIG_FILE}"
    act sudo systemctl restart avahi-daemon.service
}

# Reload the service files, so the next boot starts the services without a changed on disk warning
reload_units() {
    act sudo systemctl daemon-reload
}

# Clear the system journal and the shell history
clear_logs() {
    act sudo journalctl --rotate
    act sudo journalctl --vacuum-time=1s
    act truncate -c -s 0 -- "${SHELL_HISTORY_FILE}"
}

# Forget every saved WiFi network only when asked to, the customer joins theirs from the WiFi menu
remove_wifi() {
    # Keep them unless asked to delete
    if [[ "${DELETE_WIFI}" != "${FLAG_ON}" ]]; then
        keep_note "Keeping the saved WiFi networks"
        return
    fi

    # List the saved wireless connections
    local connection_ids
    connection_ids="$(nmcli -t -f UUID,TYPE connection show | awk -F: -v wireless="${WIRELESS_CONNECTION_TYPE}" '$2 == wireless {print $1}')"

    # Delete each one
    local connection_id
    for connection_id in ${connection_ids}; do
        act sudo nmcli connection delete uuid "${connection_id}"
    done
}

# List what was kept, so the steps above are all deletes
print_kept_notes() {
    # Nothing to list when everything goes
    if [[ ${#KEPT_NOTES[@]} -eq 0 ]]; then
        return
    fi

    # Print each kept item
    echo
    local note
    for note in "${KEPT_NOTES[@]}"; do
        echo "  ${note}"
    done
}

# Remember something that is kept, to list after the steps
keep_note() {
    KEPT_NOTES+=("$1")
}

# Run a step, or only show it on a preview
act() {
    # Show the step
    echo "  $*"

    # Stop here on a preview
    if [[ "${DRY_RUN}" == "${FLAG_ON}" ]]; then
        return
    fi

    # Run it, and carry on if one step fails
    if ! "$@"; then
        echo "    step failed, carrying on"
    fi
}

# Say what a person still has to do
print_reminders() {
    echo
    echo "Still to do by hand:"
    echo "  Set up an OpenAI key with talk/install_openai_key.sh, or set talk/config.json to local."
    echo "  Open a new terminal to sudo reboot, as this shell can write its history back on exit."
    echo "  Check the robot starts on its own after the reboot."
}

# Run the delete
main "$@"
