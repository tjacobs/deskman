#!/usr/bin/env bash
# Configure the Jetson desktop so X starts, deskman auto-logs in, and the UI stays out of the way.

# Main
main() {
    # Parse flags then configure the machine
    parse_args "$@"

    # Quit on anything but Linux
    os_name="$(uname -s)"
    if [[ "${os_name}" != "Linux" ]]; then
        echo "install_system.sh is for the Jetson Ubuntu image." >&2
        exit 1
    fi

    # Re-run as root, keep the calling user for session settings
    if [[ "${EUID}" -ne 0 ]]; then
        exec sudo --preserve-env=SUDO_USER,HOME bash "${BASH_SOURCE[0]}" "$@"
    fi

    # Resolve the user who invoked sudo
    RUN_USER="${SUDO_USER:-$(id -un)}"
    RUN_UID="$(id -u "${RUN_USER}")"
    RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6)"

    # Apply boot, login, and desktop settings
    echo "Configuring system for ${RUN_USER}"
    enable_graphical_boot
    enable_autologin
    disable_crash_dialog
    disable_software_updater
    configure_session
    disable_screen_idle
    enable_screen_keyboard
    enable_service_shortcuts
    fix_mdns_name
    echo "Done."
}

# Parse command line arguments
parse_args() {
    # Walk each argument
    for argument in "$@"; do

        # Print help and quit
        if [[ "${argument}" == "-h" || "${argument}" == "--help" ]]; then
            echo "Usage: ./install_system.sh"
            echo "  Boot to X, auto-login, black empty desktop, no crash dialogs."
            exit 0
        fi

        # Reject anything else
        echo "Unknown argument: ${argument}" >&2
        echo "Usage: ./install_system.sh" >&2
        exit 1
    done
}

# Stop on errors
set -euo pipefail

# GDM and apport paths
GDM_CONF="/etc/gdm3/custom.conf"
APPORT_CONF="/etc/default/apport"

# Avahi paths, the drop-in holds Avahi back until the network is up
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
AVAHI_DROP_IN_DIR="/etc/systemd/system/avahi-daemon.service.d"
AVAHI_DROP_IN="${AVAHI_DROP_IN_DIR}/wait-for-network.conf"

# Jetson network ports, Wi-Fi and wired carry mDNS, the rest are virtual and only cause name clashes
AVAHI_ALLOW_INTERFACES="wlP1p1s0,enP8p1s0"
AVAHI_DENY_INTERFACES="docker0,l4tbr0,usb0,usb1"

# Keep Files on the dash, leave Help, Software, and Firefox off
FAVORITE_APPS="['org.gnome.Nautilus.desktop']"

# Boot graphical.target so GDM and X start
enable_graphical_boot() {
    # Set graphical boot and start it now
    echo "Setting boot target to graphical"
    systemctl set-default graphical.target
    systemctl start graphical.target
}

# Log into the robot user on Xorg
enable_autologin() {
    # Write GDM Xorg autologin for the robot user
    echo "Enabling autologin for ${RUN_USER}"
    cat > "${GDM_CONF}" <<EOF
# GDM configuration storage
#
# See /usr/share/gdm/gdm.schemas for a list of available options.

[daemon]
# Uncomment the line below to force the login screen to use Xorg
WaylandEnable=false

# Enabling automatic login
AutomaticLoginEnable=true
AutomaticLogin=${RUN_USER}

# Enabling timed login
#  TimedLoginEnable = true
#  TimedLogin = user1
#  TimedLoginDelay = 10

[security]

[xdmcp]

[chooser]

[debug]
# Uncomment the line below to turn on debugging
# More verbose logs
# Additionally lets the X server dump core if it crashes
#Enable=true
EOF
}

# Wait for Enter, then delete the given paths
confirm_rm() {
    # Keep only paths that exist
    delete_paths=()
    for delete_path in "$@"; do
        if [[ -e "${delete_path}" ]]; then
            delete_paths+=("${delete_path}")
        fi
    done
    if [[ "${#delete_paths[@]}" -eq 0 ]]; then
        return
    fi

    # Enter confirms, Ctrl-C aborts the script
    echo "Press Enter to delete:"
    printf '  %s\n' "${delete_paths[@]}"
    read -r confirm_enter </dev/tty
    rm -rf -- "${delete_paths[@]}"
}

# Turn off Apport System program problem detected
disable_crash_dialog() {
    echo "Disabling apport crash dialogs"

    # Stop generating crash reports
    cat > "${APPORT_CONF}" <<'EOF'
# set this to 0 to disable apport, or to 1 to enable it
# you can temporarily override this with
# sudo service apport start force_start=1
enabled=0
EOF

    # Mask the service and drop leftover crash files
    systemctl stop apport.service 2>/dev/null || true
    systemctl disable apport.service 2>/dev/null || true
    systemctl mask apport.service 2>/dev/null || true
    confirm_rm /var/crash/*
}

# Turn off Software Updater and unattended apt
disable_software_updater() {
    echo "Disabling software updater dialogs"

    # Stop apt from checking for upgrades on a timer
    cat > /etc/apt/apt.conf.d/10periodic <<'EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
EOF
    cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
EOF

    # Disable the daily apt timers
    systemctl disable --now apt-daily.timer 2>/dev/null || true
    systemctl disable --now apt-daily-upgrade.timer 2>/dev/null || true
    systemctl disable --now unattended-upgrades.service 2>/dev/null || true

    # Stop the update-notifier timers that pop the updater open
    systemctl disable --now update-notifier-download.timer 2>/dev/null || true
    systemctl disable --now update-notifier-motd.timer 2>/dev/null || true

    # Never offer a release upgrade
    sed -i 's/^Prompt=.*/Prompt=never/' /etc/update-manager/release-upgrades

    # Stop the user units that launch update-manager
    run_as_user systemctl --user mask update-notifier-crash.path || true
    run_as_user systemctl --user mask update-notifier-livepatch.path || true
    run_as_user systemctl --user mask update-notifier-release.path || true

    # Keep Software Updater quiet when it is opened by hand
    run_as_user gsettings set com.ubuntu.update-manager check-dist-upgrades false
    run_as_user gsettings set com.ubuntu.update-manager first-run false
    run_as_user gsettings set com.ubuntu.update-manager show-details false
}

# Session settings for the robot user
configure_session() {
    # Skip first-login setup before changing the desktop
    echo "Configuring desktop session for ${RUN_USER}"
    skip_gnome_setup

    # Paint the desktop and lock screen black
    run_as_user gsettings set org.gnome.desktop.background picture-uri ''
    run_as_user gsettings set org.gnome.desktop.background picture-uri-dark ''
    run_as_user gsettings set org.gnome.desktop.background primary-color '#000000'
    run_as_user gsettings set org.gnome.desktop.background secondary-color '#000000'
    run_as_user gsettings set org.gnome.desktop.background color-shading-type 'solid'
    run_as_user gsettings set org.gnome.desktop.background picture-options 'none'
    run_as_user gsettings set org.gnome.desktop.screensaver picture-uri ''
    run_as_user gsettings set org.gnome.desktop.screensaver primary-color '#000000'
    run_as_user gsettings set org.gnome.desktop.screensaver picture-options 'none'

    # Keep Files on the dash only
    run_as_user gsettings set org.gnome.shell favorite-apps "${FAVORITE_APPS}"

    # Hide updater and crash popups in the session
    run_as_user gsettings set com.ubuntu.update-notifier show-apport-crashes false
    run_as_user gsettings set com.ubuntu.update-notifier no-show-notifications true
    run_as_user gsettings set com.ubuntu.update-notifier regular-auto-launch-interval 36500
    run_as_user gsettings set org.gnome.software allow-updates false
    run_as_user gsettings set org.gnome.software download-updates false
    run_as_user gsettings set org.gnome.software download-updates-notify false

    # Hide home, trash, and volume icons on the desktop and dock
    run_as_user gsettings set org.gnome.shell.extensions.ding show-home false || true
    run_as_user gsettings set org.gnome.shell.extensions.ding show-trash false || true
    run_as_user gsettings set org.gnome.shell.extensions.ding show-volumes false || true
    run_as_user gsettings set org.gnome.shell.extensions.ding show-network-volumes false || true
    run_as_user gsettings set org.gnome.shell.extensions.dash-to-dock show-trash false || true

    # Show our .desktop launchers, ding was left disabled
    run_as_user gsettings set org.gnome.shell disabled-extensions "[]" || true
    run_as_user gsettings set org.gnome.shell enabled-extensions "['ding@rastersoft.com']" || true
    confirm_rm "${RUN_HOME}/Desktop/"*.desktop
    remove_extra_home_folders
}

# Drop unused XDG folders so login does not recreate them
remove_extra_home_folders() {
    # Remove unused home folders GNOME would otherwise show
    confirm_rm "${RUN_HOME}/Music" "${RUN_HOME}/Pictures" "${RUN_HOME}/Public" "${RUN_HOME}/Templates" "${RUN_HOME}/Videos"

    # Point leftover XDG dirs at home so they are not recreated
    printf '%s\n' 'enabled=False' > "${RUN_HOME}/.config/user-dirs.conf"
    cat > "${RUN_HOME}/.config/user-dirs.dirs" <<EOF
XDG_DESKTOP_DIR="\$HOME/Desktop"
XDG_DOWNLOAD_DIR="\$HOME/Downloads"
XDG_TEMPLATES_DIR="\$HOME"
XDG_PUBLICSHARE_DIR="\$HOME"
XDG_DOCUMENTS_DIR="\$HOME/Documents"
XDG_MUSIC_DIR="\$HOME"
XDG_PICTURES_DIR="\$HOME"
XDG_VIDEOS_DIR="\$HOME"
EOF
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/user-dirs.conf" "${RUN_HOME}/.config/user-dirs.dirs"
}

# Let the GNOME and onboard keyboards show again
enable_screen_keyboard() {
    echo "Enabling the on-screen keyboard"

    # Turn on the GNOME accessibility keyboard
    run_as_user gsettings set org.gnome.desktop.a11y.applications screen-keyboard-enabled true
    run_as_user gsettings reset org.gnome.desktop.interface gtk-im-module || true

    # Let onboard show itself on text focus
    run_as_user gsettings set org.onboard.auto-show enabled true || true
    run_as_user gsettings set org.onboard.auto-show tablet-mode-detection-enabled true || true
    run_as_user gsettings set org.onboard start-minimized false || true

    # Drop the hidden autostart override so the system entry runs
    rm -f "${RUN_HOME}/.config/autostart/onboard-autostart.desktop"
}

# Desktop icons to start robot.service and teleport.service without a password
enable_service_shortcuts() {
    echo "Adding Start Robot and Start Teleport desktop shortcuts"

    # Allow this user to start those two units from the icons, and restart them by voice
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    install -m 0755 "${script_dir}/restart_services.sh" /usr/local/bin/deskman-restart-services
    sudoers_file="/etc/sudoers.d/deskman-services"
    printf '%s\n' "${RUN_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl start robot.service, /usr/bin/systemctl start teleport.service, /usr/local/bin/deskman-restart-services" > "${sudoers_file}"
    chmod 0440 "${sudoers_file}"

    # Place the launchers after the desktop wipe above
    mkdir -p "${RUN_HOME}/Desktop"
    install_robot_eyes_icon
    write_service_shortcut "Start Robot" robot "${RUN_HOME}/.local/share/icons/deskman-robot.svg"
    write_service_shortcut "Start Teleport" teleport camera-web
}

# White face with two black eyes, same look as the robot window
install_robot_eyes_icon() {
    icon_dir="${RUN_HOME}/.local/share/icons"
    icon_file="${icon_dir}/deskman-robot.svg"
    mkdir -p "${icon_dir}"

    # Copy from the repo when present, else write the drawing here
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    source_icon="${script_dir}/icons/robot-eyes.svg"
    if [[ -f "${source_icon}" ]]; then
        cp "${source_icon}" "${icon_file}"
    else
        cat > "${icon_file}" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="16" fill="#ffffff"/>
  <ellipse cx="44" cy="64" rx="14" ry="32" fill="#000000"/>
  <ellipse cx="84" cy="64" rx="14" ry="32" fill="#000000"/>
</svg>
EOF
    fi
    chown "${RUN_USER}:${RUN_USER}" "${icon_file}"
}

# One desktop launcher that starts a systemd unit
write_service_shortcut() {
    launcher_name="$1"
    service_name="$2"
    icon_name="$3"
    desktop_file="${RUN_HOME}/Desktop/${service_name}.desktop"

    # Write a trusted launcher the desktop will run on tap
    cat > "${desktop_file}" <<EOF
[Desktop Entry]
Type=Application
Name=${launcher_name}
Comment=Start ${service_name}.service
Exec=sudo -n /usr/bin/systemctl start ${service_name}.service
Icon=${icon_name}
Terminal=false
Categories=Utility;
EOF
    chown "${RUN_USER}:${RUN_USER}" "${desktop_file}"
    chmod 0755 "${desktop_file}"
    run_as_user gio set "${desktop_file}" metadata::trusted true || true
}

# Keep the display on and skip the lock screen
disable_screen_idle() {
    echo "Disabling screen blanking and screensaver"

    # Never idle into screensaver or lock
    run_as_user gsettings set org.gnome.desktop.session idle-delay 0
    run_as_user gsettings set org.gnome.desktop.screensaver lock-enabled false
    run_as_user gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
    run_as_user gsettings set org.gnome.desktop.screensaver lock-delay 0
    run_as_user gsettings set org.gnome.desktop.screensaver ubuntu-lock-on-suspend false || true
    run_as_user gsettings set org.gnome.desktop.lockdown disable-lock-screen true

    # Do not dim, sleep, or suspend from idle
    run_as_user gsettings set org.gnome.settings-daemon.plugins.power idle-dim false || true
    run_as_user gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' || true
    run_as_user gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' || true
    run_as_user gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0 || true
    run_as_user gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0 || true

    # Ignore logind idle so the session stays logged in
    mkdir -p /etc/systemd/logind.conf.d
    cat > /etc/systemd/logind.conf.d/disable-idle.conf <<'EOF'
[Login]
IdleAction=ignore
EOF

    # Turn off X screensaver and DPMS at login
    cat > "${RUN_HOME}/.config/autostart/disable-screen-blank.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Disable screen blank
Exec=sh -c "xset s off; xset s noblank; xset -dpms"
X-GNOME-Autostart-enabled=true
EOF
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart/disable-screen-blank.desktop"
}

# Publish this machine as hostname.local, Avahi renames itself when IPv6 addresses come and go
fix_mdns_name() {
    host_name="$(cat /etc/hostname)"
    echo "Publishing mDNS name ${host_name}.local"

    # Resolve our own name locally, so lookups work before Avahi answers
    if ! grep -q "^127.0.1.1[[:space:]]" /etc/hosts; then
        printf '\n%s\n' "127.0.1.1	${host_name} ${host_name}.local" >> /etc/hosts
    fi

    # Claim the name from the hostname, not from a leftover announcement
    set_avahi_option host-name "${host_name}"

    # Skip IPv6 sockets, Avahi still publishes AAAA on IPv4 unless this is off too
    set_avahi_option use-ipv4 yes
    set_avahi_option use-ipv6 no
    set_avahi_option publish-aaaa-on-ipv4 no

    # Watch only the real network ports, docker and the USB gadget bridge churn addresses and trip the same race
    set_avahi_option allow-interfaces "${AVAHI_ALLOW_INTERFACES}"
    set_avahi_option deny-interfaces "${AVAHI_DENY_INTERFACES}"

    # Start Avahi once the link has an address, else it collides with its own first claim
    mkdir -p "${AVAHI_DROP_IN_DIR}"
    cat > "${AVAHI_DROP_IN}" <<'EOF'
[Unit]
After=network-online.target
Wants=network-online.target
EOF

    # Wait for the network at boot, the drop-in needs this target to mean something
    systemctl enable NetworkManager-wait-online.service 2>/dev/null || true

    # Pick up the drop-in and republish under the right name
    systemctl daemon-reload
    systemctl restart avahi-daemon.service
}

# Skip Connect your online accounts and first-login setup
skip_gnome_setup() {
    # Stop the first-login wizard if it is already running
    pkill -u "${RUN_USER}" -f gnome-initial-setup || true

    # Mark first-login setup already done
    sudo -u "${RUN_USER}" mkdir -p "${RUN_HOME}/.config/autostart" "${RUN_HOME}/Desktop"
    echo yes > "${RUN_HOME}/.config/gnome-initial-setup-done"
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/gnome-initial-setup-done"

    # Hide first-login, updater, and folder-restore autostart entries
    hide_autostart gnome-initial-setup-first-login.desktop
    hide_autostart gnome-initial-setup-copy-worker.desktop
    hide_autostart update-notifier.desktop
    hide_autostart gnome-software-service.desktop
    hide_autostart update-manager.desktop
    hide_autostart user-dirs-update-gtk.desktop

    # Stop updater windows already running
    pkill -u "${RUN_USER}" -f update-notifier || true
    pkill -u "${RUN_USER}" -f update-manager || true
    pkill -u "${RUN_USER}" -f gnome-software || true
}

# Stop the session starting one autostart entry, the session drops entries it cannot parse
hide_autostart() {
    entry_name="$1"
    system_entry="/etc/xdg/autostart/${entry_name}"
    user_entry="${RUN_HOME}/.config/autostart/${entry_name}"

    # Copy the system entry so every key the session needs is there
    if [[ -f "${system_entry}" ]]; then
        grep -v -e '^Hidden=' -e '^X-GNOME-Autostart-enabled=' "${system_entry}" > "${user_entry}"
    else
        printf '%s\n' '[Desktop Entry]' 'Type=Application' "Name=${entry_name}" 'Exec=/bin/true' 'NoDisplay=true' > "${user_entry}"
    fi

    # Mark it hidden, which the session reads as deleted
    printf '%s\n' 'Hidden=true' 'X-GNOME-Autostart-enabled=false' >> "${user_entry}"
    chown "${RUN_USER}:${RUN_USER}" "${user_entry}"
}

# Run a command as the install user on their session bus
run_as_user() {
    # Build the user environment
    user_environment=(HOME="${RUN_HOME}" USER="${RUN_USER}" LOGNAME="${RUN_USER}" XDG_RUNTIME_DIR="/run/user/${RUN_UID}")

    # Reuse the logged-in bus so gsettings stays quiet
    if [[ -S "/run/user/${RUN_UID}/bus" ]]; then
        sudo -u "${RUN_USER}" env "${user_environment[@]}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${RUN_UID}/bus" "$@" >/dev/null
        return
    fi

    # Fall back to one private bus and hide dbus-daemon chatter
    sudo -u "${RUN_USER}" env "${user_environment[@]}" dbus-run-session -- "$@" >/dev/null 2>&1
}

# Set one option in the Avahi config, the stock file ships these keys commented out
set_avahi_option() {
    option_name="$1"
    option_value="$2"

    # Overwrite the key whether it is commented out or already set
    if grep -qE "^#*${option_name}=" "${AVAHI_CONF}"; then
        sed -i "s/^#*${option_name}=.*/${option_name}=${option_value}/" "${AVAHI_CONF}"
        return
    fi

    # Add it under the server section when the key is missing
    sed -i "/^\[server\]/a ${option_name}=${option_value}" "${AVAHI_CONF}"
}

# Run install
main "$@"
