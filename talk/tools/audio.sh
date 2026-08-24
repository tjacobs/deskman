#!/usr/bin/env bash
# Route audio to the USB soundcard and disable onboard HDMI audio, on a raspberry pi or a jetson.
# Usage: ./tools/audio.sh

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UDEV_RULE_PATH="/etc/udev/rules.d/99-speak-usb-audio.rules"
SETUP_LINK_PATH="/usr/local/bin/speak-usb-audio"
SYSTEM_ASOUND_PATH="/etc/asound.conf"
USER_ASOUND_NAME=".asoundrc"
BLACKLIST_PATH="/etc/modprobe.d/blacklist-speak-internal-audio.conf"
PULSE_SINK_NAME="speak_usb"
PULSE_DEFAULT_PA_NAME="default.pa"

# Config the account to set up when sudo does not name one, udev runs with no sudo user
LOGIN_USER_ID=1000

# Config the raspberry pi boot settings that create the HDMI sound cards
BOOT_CONFIG_PATHS=(/boot/firmware/config.txt /boot/config.txt)
BOOT_CONFIG_BACKUP_SUFFIX=".speak-backup"
VC4_OVERLAY_MATCH="^dtoverlay=vc4-kms-v3d"
NO_AUDIO_PARAM="noaudio"
ONBOARD_AUDIO_ON="^dtparam=audio=on"
ONBOARD_AUDIO_OFF="dtparam=audio=off"
DEVICE_TREE_MODEL="/proc/device-tree/model"
RASPBERRY_PI_MATCH="Raspberry Pi"

# State
TARGET_USER=""
TARGET_HOME=""
TARGET_USER_ID=""
REBOOT_NEEDED="false"

# Main
main() {
    parse_args "$@"
    become_root "$@"
    find_target_user
    configure_audio
    install_udev_rule
    disable_internal_audio
    print_done
}

# Parse command line arguments
parse_args() {
    for argument in "$@"; do
        if [[ "${argument}" == "-h" || "${argument}" == "--help" ]]; then
            print_usage
            exit 0
        fi
        echo "Unknown argument: ${argument}"
        print_usage
        exit 1
    done
}

# Print usage help
print_usage() {
    echo "Usage: ./tools/audio.sh"
    echo "  Routes ALSA and pulse to the USB soundcard, for this user and for services."
    echo "  Disables onboard HDMI audio, and sets audio up again when a card is replugged."
    echo "  Asks for sudo, since it writes system config."
}

# Re-run under sudo, the system config and the boot config need root
become_root() {
    # Skip when already root, udev runs this as root
    if [[ "${EUID}" -eq 0 ]]; then
        return 0
    fi
    exec sudo bash "${BASH_SOURCE[0]}" "$@"
}

# Find the login account whose audio to configure
find_target_user() {
    # Prefer the user who called sudo
    TARGET_USER="${SUDO_USER:-}"

    # Fall back to the owner of this repo, then the configured login id
    if [[ -z "${TARGET_USER}" || "${TARGET_USER}" == "root" ]]; then
        TARGET_USER="$(stat -c '%U' "${SCRIPT_DIR}" 2>/dev/null || true)"
    fi
    if [[ -z "${TARGET_USER}" || "${TARGET_USER}" == "root" ]]; then
        TARGET_USER="$(getent passwd "${LOGIN_USER_ID}" | cut -d: -f1)"
    fi

    # Quit when there is no login account
    if [[ -z "${TARGET_USER}" ]]; then
        echo "No login user found to configure."
        exit 1
    fi

    # Read the home directory and id for later steps
    TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
    TARGET_USER_ID="$(id -u "${TARGET_USER}")"
}

# Point ALSA and pulse at the USB soundcard
configure_audio() {
    # Quit when no USB soundcard is plugged in
    local card_index card_name
    card_index="$(find_usb_card || true)"
    if [[ -z "${card_index}" ]]; then
        echo "No USB soundcard found. Plug one in and run ./tools/audio.sh again."
        exit 1
    fi

    # Write the config for this user, and for services that run as root
    card_name="$(cat "/proc/asound/card${card_index}/id")"
    write_asound_config "${card_name}" "${TARGET_HOME}/${USER_ASOUND_NAME}"
    chown "${TARGET_USER}:${TARGET_USER}" "${TARGET_HOME}/${USER_ASOUND_NAME}"
    write_asound_config "${card_name}" "${SYSTEM_ASOUND_PATH}"

    # Turn the card up and hand it to pulse
    set_usb_volume "${card_index}"
    write_pulse_default_pa "${card_name}"
    configure_pulse_runtime "${card_index}" "${card_name}"

    echo "USB card ${card_index}: ${card_name}"
    echo "Wrote ${TARGET_HOME}/${USER_ASOUND_NAME} and ${SYSTEM_ASOUND_PATH}"
}

# Return card index for the USB sound device
find_usb_card() {
    local line card_index usb_cards=()

    # Scan proc sound cards for USB audio
    while IFS= read -r line; do
        if [[ "${line}" =~ ^[[:space:]]*([0-9]+)[[:space:]]+.*USB-Audio ]]; then
            card_index="${BASH_REMATCH[1]}"
            if [[ -d "/sys/class/sound/card${card_index}" ]]; then
                usb_cards+=("${card_index}")
            fi
        fi
    done < /proc/asound/cards

    # Prefer the speaker only card, one without a mic
    for card_index in "${usb_cards[@]}"; do
        if ! card_has_capture "${card_index}"; then
            echo "${card_index}"
            return 0
        fi
    done

    # Otherwise take the first USB card
    if [[ "${#usb_cards[@]}" -gt 0 ]]; then
        echo "${usb_cards[0]}"
        return 0
    fi
    return 1
}

# Return true when a card has a capture stream
card_has_capture() {
    local card_index="$1"
    grep -q 'Capture:' "/proc/asound/card${card_index}/stream0" 2>/dev/null
}

# Write an ALSA config that defaults to one card
write_asound_config() {
    local card_name="$1"
    local output_path="$2"

    # Name the card rather than number it, indexes shift when other cards come and go
    cat > "${output_path}" <<EOF
# Written by speak tools/audio.sh, defaults ALSA to the USB soundcard
pcm.!default {
    type plug
    slave.pcm "hw:CARD=${card_name},DEV=0"
}

ctl.!default {
    type hw
    card ${card_name}
}
EOF
}

# Set the USB card volume to full
set_usb_volume() {
    local card_index="$1"

    # Try common mixer names, USB dongles use Speaker, PCM, or Master
    if command -v amixer >/dev/null 2>&1; then
        amixer -c "${card_index}" set Speaker 100% unmute >/dev/null 2>&1 || true
        amixer -c "${card_index}" set PCM 100% unmute >/dev/null 2>&1 || true
        amixer -c "${card_index}" set Master 100% unmute >/dev/null 2>&1 || true
    fi
}

# Persist a pulse startup file that loads the USB sink after reboot
write_pulse_default_pa() {
    local card_name="$1"
    local pulse_dir="${TARGET_HOME}/.config/pulse"
    local output_path="${pulse_dir}/${PULSE_DEFAULT_PA_NAME}"

    # Keep the system defaults, then force the USB sink so aplay is not Dummy Output
    mkdir -p "${pulse_dir}"
    cat > "${output_path}" <<EOF
# Written by speak tools/audio.sh
.include /etc/pulse/default.pa

.nofail
load-module module-alsa-sink device=hw:CARD=${card_name},DEV=0 sink_name=${PULSE_SINK_NAME}
set-default-sink ${PULSE_SINK_NAME}
.fail
EOF
    chown -R "${TARGET_USER}:${TARGET_USER}" "${pulse_dir}"
    echo "Wrote ${output_path}"
}

# Turn off the onboard pulse cards and pick the USB sink
configure_pulse_runtime() {
    local card_index="$1"
    local card_name="$2"

    # Skip when pulse is not running for the login user
    if ! run_as_user pactl info >/dev/null 2>&1; then
        echo "Pulse is not running for ${TARGET_USER}, wrote startup config only."
        return 0
    fi

    # Disable every card that is not USB
    local card sink default_sink_file
    while IFS= read -r card; do
        if [[ "${card}" != *usb* && "${card}" != *${card_name}* ]]; then
            run_as_user pactl set-card-profile "${card}" off 2>/dev/null || true
        fi
    done < <(run_as_user pactl list cards short | awk '{print $2}')

    # Load or find the USB sink, pulse often only has Dummy Output until this runs
    sink="$(ensure_pulse_usb_sink "${card_index}" "${card_name}")"
    if [[ -z "${sink}" ]]; then
        echo "Could not create a Pulse USB sink."
        return 0
    fi

    # Make it the default, unmuted and full
    run_as_user pactl set-default-sink "${sink}"
    run_as_user pactl set-sink-volume "${sink}" 100% 2>/dev/null || true
    run_as_user pactl set-sink-mute "${sink}" 0 2>/dev/null || true

    # Persist the stable sink name for the next pulse start
    mkdir -p "${TARGET_HOME}/.config/pulse"
    shopt -s nullglob
    for default_sink_file in "${TARGET_HOME}/.config/pulse/"*-default-sink; do
        echo "${PULSE_SINK_NAME}" > "${default_sink_file}"
        chown "${TARGET_USER}:${TARGET_USER}" "${default_sink_file}"
    done
    shopt -u nullglob
    echo "Pulse default sink: ${sink}"
}

# Return a Pulse sink for the USB card, loading one when missing
ensure_pulse_usb_sink() {
    local card_index="$1"
    local card_name="$2"
    local sink

    # Prefer the stable name this script installs
    sink="$(find_pulse_sink_by_name "${PULSE_SINK_NAME}" || true)"
    if [[ -n "${sink}" ]]; then
        echo "${sink}"
        return 0
    fi

    # Reuse an existing USB or hw sink when pulse already has one
    sink="$(find_existing_pulse_usb_sink "${card_index}" "${card_name}" || true)"
    if [[ -n "${sink}" ]]; then
        echo "${sink}"
        return 0
    fi

    # Load a named sink so aplay through pulse reaches the speaker
    run_as_user pactl load-module module-alsa-sink "device=hw:CARD=${card_name},DEV=0" "sink_name=${PULSE_SINK_NAME}" >/dev/null 2>&1 || true
    sink="$(find_pulse_sink_by_name "${PULSE_SINK_NAME}" || true)"
    if [[ -n "${sink}" ]]; then
        echo "${sink}"
        return 0
    fi

    # Fall back to the numeric device string when the card name form fails
    run_as_user pactl load-module module-alsa-sink "device=hw:${card_index},0" "sink_name=${PULSE_SINK_NAME}" >/dev/null 2>&1 || true
    find_pulse_sink_by_name "${PULSE_SINK_NAME}"
}

# Return a sink name when it already exists
find_pulse_sink_by_name() {
    local wanted="$1"
    local sink
    while IFS= read -r sink; do
        if [[ "${sink}" == "${wanted}" ]]; then
            echo "${sink}"
            return 0
        fi
    done < <(run_as_user pactl list sinks short | awk '{print $2}')
    return 1
}

# Return any current non-dummy sink that looks like the USB card
find_existing_pulse_usb_sink() {
    local card_index="$1"
    local card_name="$2"
    local sink
    local card_name_lower
    card_name_lower="$(echo "${card_name}" | tr '[:upper:]' '[:lower:]')"

    while IFS= read -r sink; do
        if [[ "${sink}" == "auto_null" || "${sink}" == *.monitor ]]; then
            continue
        fi
        if [[ "${sink}" == *usb* || "${sink}" == *"${card_name_lower}"* || "${sink}" == "alsa_output.hw_${card_index}_0" ]]; then
            echo "${sink}"
            return 0
        fi
    done < <(run_as_user pactl list sinks short | awk '{print $2}')
    return 1
}

# Install the udev rule so a replugged card is set up again
install_udev_rule() {
    # Link the script where udev can reach it
    ln -sf "${SCRIPT_DIR}/audio.sh" "${SETUP_LINK_PATH}"
    chmod 755 "${SCRIPT_DIR}/audio.sh"

    # Run as root on plug, the script finds the login user and configures their pulse
    cat > "${UDEV_RULE_PATH}" <<EOF
# Reconfigure audio when a USB soundcard is plugged in
ACTION=="add", SUBSYSTEM=="sound", KERNEL=="card*", ENV{ID_BUS}=="usb", RUN+="${SETUP_LINK_PATH}"
EOF

    # Load the rule, the current card is already configured so do not trigger
    udevadm control --reload-rules
    echo "Installed ${UDEV_RULE_PATH}"
}

# Keep the onboard cards out of ALSA so only USB cards register
disable_internal_audio() {
    # A raspberry pi creates its HDMI cards from the boot config, not from a module
    if is_raspberry_pi; then
        disable_pi_hdmi_audio
        return 0
    fi

    install_blacklist
}

# Turn off HDMI and onboard audio in the raspberry pi boot config
disable_pi_hdmi_audio() {
    # Quit when no boot config is present
    local config_path before after
    config_path="$(find_boot_config || true)"
    if [[ -z "${config_path}" ]]; then
        echo "No boot config found, leaving HDMI audio enabled."
        return 0
    fi

    # Keep one backup of the original so the change can be undone
    if [[ ! -f "${config_path}${BOOT_CONFIG_BACKUP_SUFFIX}" ]]; then
        cp "${config_path}" "${config_path}${BOOT_CONFIG_BACKUP_SUFFIX}"
    fi

    # Add noaudio to the graphics overlay so the HDMI cards never register
    before="$(md5sum < "${config_path}")"
    sed -i -E "/${VC4_OVERLAY_MATCH}/{/${NO_AUDIO_PARAM}/! s/\$/,${NO_AUDIO_PARAM}/}" "${config_path}"

    # Turn off the onboard card as well, nothing is wired to it
    sed -i -E "s/${ONBOARD_AUDIO_ON}/${ONBOARD_AUDIO_OFF}/" "${config_path}"

    # Note the change, the cards only go away on reboot
    after="$(md5sum < "${config_path}")"
    if [[ "${before}" != "${after}" ]]; then
        REBOOT_NEEDED="true"
        echo "Disabled HDMI audio in ${config_path}"
    fi
}

# Return the boot config path, empty when missing
find_boot_config() {
    # Check each known location, the path moved in newer raspberry pi os
    for path in "${BOOT_CONFIG_PATHS[@]}"; do
        if [[ -f "${path}" ]]; then
            echo "${path}"
            return 0
        fi
    done
}

# Blacklist the jetson internal audio drivers so only USB cards register
install_blacklist() {
    # Skip when already blacklisted
    if [[ -f "${BLACKLIST_PATH}" ]]; then
        return 0
    fi

    cat > "${BLACKLIST_PATH}" <<EOF
# Added by speak tools/audio.sh, keeps HDMI and APE audio out of ALSA
blacklist snd_hda_tegra
install snd_hda_tegra /bin/false
blacklist snd_soc_tegra_machine_driver
install snd_soc_tegra_machine_driver /bin/false
EOF

    # Unload now so a reboot is not required
    rmmod snd_hda_tegra 2>/dev/null || true
    rmmod snd_soc_tegra_machine_driver 2>/dev/null || true
    echo "Installed ${BLACKLIST_PATH}"
}

# Print what to do next
print_done() {
    echo "Audio set up for ${TARGET_USER}."

    # Ask for a reboot only when the boot config changed
    if [[ "${REBOOT_NEEDED}" == "true" ]]; then
        echo "Reboot to drop the HDMI sound cards."
    fi
}

# Run a command as the login user, with their session so pulse is reachable
run_as_user() {
    runuser -u "${TARGET_USER}" -- env "XDG_RUNTIME_DIR=/run/user/${TARGET_USER_ID}" "$@"
}

# Return true when running on a raspberry pi
is_raspberry_pi() {
    # Read the board name the firmware exposes, missing on other machines
    if [[ -r "${DEVICE_TREE_MODEL}" ]] && tr -d '\0' < "${DEVICE_TREE_MODEL}" | grep -q "${RASPBERRY_PI_MATCH}"; then
        return 0
    fi
    return 1
}

main "$@"
