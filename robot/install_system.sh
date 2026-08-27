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
    disable_screen_keyboard
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

    # Hide home, trash, and volume icons on the desktop
    run_as_user gsettings set org.gnome.shell.extensions.ding show-home false || true
    run_as_user gsettings set org.gnome.shell.extensions.ding show-trash false || true
    run_as_user gsettings set org.gnome.shell.extensions.ding show-volumes false || true
    run_as_user gsettings set org.gnome.shell.extensions.ding show-network-volumes false || true
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

# Keep the on-screen keyboard from popping over the robot face
disable_screen_keyboard() {
    echo "Disabling the on-screen keyboard"

    # Turn off the GNOME accessibility keyboard
    run_as_user gsettings set org.gnome.desktop.a11y.applications screen-keyboard-enabled false
    run_as_user gsettings set org.gnome.desktop.interface gtk-im-module '' || true

    # Stop onboard from showing itself on text focus
    run_as_user gsettings set org.onboard.auto-show enabled false || true
    run_as_user gsettings set org.onboard.auto-show tablet-mode-detection-enabled false || true
    run_as_user gsettings set org.onboard start-minimized true || true

    # Hide the onboard autostart entry and close it if it is up
    cat > "${RUN_HOME}/.config/autostart/onboard-autostart.desktop" <<'EOF'
[Desktop Entry]
Name=Onboard
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart/onboard-autostart.desktop"
    pkill -u "${RUN_USER}" -x onboard || true
}

# Keep the display on and skip the lock screen
disable_screen_idle() {
    echo "Disabling screen blanking, screensaver, and lock"

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

# Skip Connect your online accounts and first-login setup
skip_gnome_setup() {
    # Stop the first-login wizard if it is already running
    pkill -u "${RUN_USER}" -f gnome-initial-setup || true

    # Mark first-login setup already done
    sudo -u "${RUN_USER}" mkdir -p "${RUN_HOME}/.config/autostart" "${RUN_HOME}/Desktop"
    echo yes > "${RUN_HOME}/.config/gnome-initial-setup-done"
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/gnome-initial-setup-done"

    # Hide first-login, updater, and folder-restore autostart entries
    cat > "${RUN_HOME}/.config/autostart/gnome-initial-setup-first-login.desktop" <<'EOF'
[Desktop Entry]
Name=GNOME Initial Setup
Exec=/usr/libexec/gnome-initial-setup --existing-user
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    cat > "${RUN_HOME}/.config/autostart/gnome-initial-setup-copy-worker.desktop" <<'EOF'
[Desktop Entry]
Name=GNOME Initial Setup Copy Worker
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    cat > "${RUN_HOME}/.config/autostart/update-notifier.desktop" <<'EOF'
[Desktop Entry]
Name=Update Notifier
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    cat > "${RUN_HOME}/.config/autostart/gnome-software-service.desktop" <<'EOF'
[Desktop Entry]
Name=GNOME Software
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    cat > "${RUN_HOME}/.config/autostart/update-manager.desktop" <<'EOF'
[Desktop Entry]
Name=Software Updater
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    cat > "${RUN_HOME}/.config/autostart/user-dirs-update-gtk.desktop" <<'EOF'
[Desktop Entry]
Name=User folders update
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    chown -R "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart"

    # Stop updater windows already running
    pkill -u "${RUN_USER}" -f update-notifier || true
    pkill -u "${RUN_USER}" -f update-manager || true
    pkill -u "${RUN_USER}" -f gnome-software || true
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

# Run install
main "$@"
