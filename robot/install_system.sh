#!/usr/bin/env bash
# Configure Jetson Ubuntu or Raspberry Pi OS so X starts, deskman auto-logs in, and the UI stays out of the way.

# Main
main() {
    # Parse flags then configure the machine
    parse_args "$@"

    # Quit on anything but Linux
    os_name="$(uname -s)"
    if [[ "${os_name}" != "Linux" ]]; then
        echo "install_system.sh is for Jetson Ubuntu or Raspberry Pi OS." >&2
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

    # Create the folders hide_autostart and the desktop write into
    prepare_user_dirs

    # Pick Jetson GNOME or Pi LXDE, then apply boot, login, and desktop settings
    detect_machine
    echo "Configuring ${MACHINE} for ${RUN_USER}"
    enable_graphical_boot
    enable_autologin
    disable_hot_surface_alert
    disable_crash_dialog
    disable_software_updater
    configure_session
    configure_dsi_panel
    configure_servo_uart
    configure_i2c_arm
    persist_display_rotation
    hide_mouse_pointer
    disable_screen_idle
    enable_service_shortcuts
    enable_screen_keyboard
    keep_wifi_awake
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
            echo "  Boot to X, auto-login, black empty desktop. Jetson Ubuntu or Raspberry Pi OS."
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

# Display manager and apport paths
GDM_CONF="/etc/gdm3/custom.conf"
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
APPORT_CONF="/etc/default/apport"
GETTY_AUTOLOGIN_DIR="/etc/systemd/system/getty@tty1.service.d"
GETTY_AUTOLOGIN_CONF="${GETTY_AUTOLOGIN_DIR}/autologin.conf"

# Set in detect_machine to jetson or pi
MACHINE=""

# NetworkManager drop-in that keeps the Wi-Fi radio awake for calls
NETWORK_MANAGER_CONF_DIR="/etc/NetworkManager/conf.d"
WIFI_POWER_SAVE_CONF_NAME="10-wifi-no-power-save.conf"

# Avahi paths, the drop-in holds Avahi back until the network is up
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
AVAHI_DROP_IN_DIR="/etc/systemd/system/avahi-daemon.service.d"
AVAHI_DROP_IN="${AVAHI_DROP_IN_DIR}/wait-for-network.conf"

# Jetson network ports, Wi-Fi and wired carry mDNS, the rest are virtual and only cause name clashes
AVAHI_ALLOW_INTERFACES="wlP1p1s0,enP8p1s0"
AVAHI_DENY_INTERFACES="docker0,l4tbr0,usb0,usb1"

# Keep Files on the dash, leave Help, Software, and Firefox off
FAVORITE_APPS="['org.gnome.Nautilus.desktop']"

# Pi LXDE desktop color, no wallpaper image
PI_DESKTOP_BG="#000000"

# Stock Pi OS wallpaper files, copied when this user has none yet
PI_PCMANFM_DEFAULT="/etc/xdg/pcmanfm/default"

# Raspberry Pi boot config, and the Waveshare DSI overlay, one driver board covers the 7, 8, and 10.1 inch panels and all of them run 1280x800
BOOT_CONFIG="/boot/firmware/config.txt"
PANEL_OVERLAY="vc4-kms-dsi-waveshare-panel,8_0_inch"
PANEL_COMMENT="Waveshare Rev2.1 driver board, 7 inch 1280x800 panel, I2C0, CAM/DISP 1"
SERVO_UART_OVERLAY="uart0-pi5"
SERVO_UART_COMMENT="GPIO 14/15 UART for the STS3215 servo bus"
I2C_ARM_PARAM="i2c_arm=on"
I2C_ARM_COMMENT="INA219 battery meter on GPIO 2 and 3"

# Panel output, mode, and rotation
PANEL_OUTPUT="DSI-2"
PANEL_MODE="1280x800"
PANEL_TRANSFORM="270"

# Touch keyboard paths, matchbox sets no window class so openbox matches it on the title
MATCHBOX_LAYOUT_DIR="/usr/share/matchbox-keyboard"
OPENBOX_CONFIG_NAME="lxde-pi-rc.xml"
OPENBOX_KEYBOARD_RULE='<application title="Keyboard"><decor>no</decor><layer>above</layer><position force="yes"><x>0</x><y>-0</y></position><focus>no</focus><skip_taskbar>yes</skip_taskbar><skip_pager>yes</skip_pager></application>'
KEYBOARD_LAUNCHER_NAME="deskman-keyboard.desktop"

# Cursor theme holding one transparent pixel, so the compositor never draws a pointer
CURSOR_THEME_NAME="blank"
CURSOR_POINTER_NAMES="left_ptr pointer arrow top_left_arrow xterm text hand hand1 hand2 grab grabbing watch wait progress crosshair help question_arrow move fleur all-scroll not-allowed no-drop dnd-move dnd-copy col-resize row-resize e-resize n-resize s-resize w-resize ne-resize nw-resize se-resize sw-resize ew-resize ns-resize nesw-resize nwse-resize left_side right_side top_side bottom_side sb_h_double_arrow sb_v_double_arrow"

# Create the user config folders a first graphical login would
prepare_user_dirs() {
    # Make the folders hide_autostart and the desktop icons write into
    mkdir -p "${RUN_HOME}/.config/autostart" "${RUN_HOME}/.config/pcmanfm/default" "${RUN_HOME}/.config/pcmanfm/LXDE-pi" "${RUN_HOME}/Desktop" "${RUN_HOME}/.local/share/icons"

    # Own them as the login user, root created them
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config" "${RUN_HOME}/Desktop" "${RUN_HOME}/.local" "${RUN_HOME}/.local/share" 2>/dev/null || true
    chown -R "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart" "${RUN_HOME}/.config/pcmanfm" "${RUN_HOME}/Desktop" "${RUN_HOME}/.local/share/icons"
}

# Choose Jetson when Tegra or GDM is present, otherwise Raspberry Pi LightDM
detect_machine() {
    if [[ -f /etc/nv_tegra_release || -d /etc/gdm3 ]]; then
        MACHINE="jetson"
        return
    fi
    MACHINE="pi"
}

# Boot graphical.target so the display manager and X start
enable_graphical_boot() {
    # Set graphical boot and start it now
    echo "Setting boot target to graphical"
    systemctl set-default graphical.target
    systemctl start graphical.target
}

# Log into the robot user on Xorg
enable_autologin() {
    echo "Enabling autologin for ${RUN_USER}"
    if [[ -d /etc/gdm3 ]]; then
        enable_gdm_autologin
        return
    fi
    if [[ -f "${LIGHTDM_CONF}" ]]; then
        enable_lightdm_autologin
        return
    fi
    echo "No GDM or LightDM config, skip autologin"
}

# Write GDM Xorg autologin for the Jetson robot user
enable_gdm_autologin() {
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

# Set LightDM and console autologin the same way raspi-config does
enable_lightdm_autologin() {
    set_lightdm_key autologin-user "${RUN_USER}"

    # Use the same session the greeter would, so labwc starts on a fresh Pi
    if grep -qE '^user-session=' "${LIGHTDM_CONF}"; then
        user_session="$(sed -n 's/^user-session=//p' "${LIGHTDM_CONF}" | head -n 1)"
        set_lightdm_key autologin-session "${user_session}"
    fi

    # Console login on tty1, so a dropped X session still comes back as this user
    mkdir -p "${GETTY_AUTOLOGIN_DIR}"
    cat > "${GETTY_AUTOLOGIN_CONF}" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${RUN_USER} --noclear %I \$TERM
EOF
}

# Set or uncomment one LightDM seat key
set_lightdm_key() {
    option_name="$1"
    option_value="$2"

    # Replace an already-active key
    if grep -qE "^${option_name}=" "${LIGHTDM_CONF}"; then
        sed -i "s/^${option_name}=.*/${option_name}=${option_value}/" "${LIGHTDM_CONF}"
        return
    fi

    # Uncomment the stock key
    if grep -qE "^#${option_name}=" "${LIGHTDM_CONF}"; then
        sed -i "s/^#${option_name}=.*/${option_name}=${option_value}/" "${LIGHTDM_CONF}"
        return
    fi

    # Add it under the seat section, or create that section on a stripped config
    if grep -qE '^\[Seat:\*\]' "${LIGHTDM_CONF}"; then
        sed -i "/^\[Seat:\*\]/a ${option_name}=${option_value}" "${LIGHTDM_CONF}"
        return
    fi
    printf '\n%s\n%s\n' '[Seat:*]' "${option_name}=${option_value}" >> "${LIGHTDM_CONF}"
}

# Delete the given paths when they exist, no prompt so a fresh Pi can install unattended
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

    # Show what is going away, then remove it
    echo "Deleting:"
    printf '  %s\n' "${delete_paths[@]}"
    rm -rf -- "${delete_paths[@]}"
}

# Stop nvpmodel from popping Caution, Hot surface, Do Not Touch
disable_hot_surface_alert() {
    if [[ "${MACHINE}" != "jetson" ]]; then
        return
    fi
    echo "Disabling hot surface warning"

    # Hide the tray indicator that shows that dialog
    hide_autostart nvpmodel_indicator.desktop
    pkill -u "${RUN_USER}" -f nvpmodel_indicator.py || true
}

# Turn off Apport System program problem detected
disable_crash_dialog() {
    if [[ ! -f "${APPORT_CONF}" ]]; then
        return
    fi
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
    if [[ "${MACHINE}" != "jetson" ]]; then
        return
    fi
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
    echo "Configuring desktop session for ${RUN_USER}"
    if [[ "${MACHINE}" == "pi" ]]; then
        configure_pi_session
        return
    fi

    # Skip first-login setup before changing the GNOME desktop
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
    remove_extra_desktop_launchers
    remove_extra_home_folders
}

# Black desktop on Raspberry Pi OS, keep existing icon positions
configure_pi_session() {
    hide_autostart pprompt.desktop
    hide_autostart print-applet.desktop
    hide_autostart user-dirs-update-gtk.desktop

    # Copy stock wallpaper files, Trixie keeps them under default not LXDE-pi
    seed_pi_desktop_conf

    # Paint each pcmanfm desktop profile black
    shopt -s nullglob
    for desktop_conf in "${RUN_HOME}/.config/pcmanfm/"*/desktop-items-*.conf; do
        set_desktop_conf_key "${desktop_conf}" wallpaper_mode color
        set_desktop_conf_key "${desktop_conf}" desktop_bg "${PI_DESKTOP_BG}"
        set_desktop_conf_key "${desktop_conf}" desktop_shadow "${PI_DESKTOP_BG}"
        set_desktop_conf_key "${desktop_conf}" show_trash 0
        set_desktop_conf_key "${desktop_conf}" show_mounts 0
        set_desktop_conf_key "${desktop_conf}" show_documents 0
        chown "${RUN_USER}:${RUN_USER}" "${desktop_conf}"
    done
    shopt -u nullglob

    remove_extra_desktop_launchers
    remove_extra_home_folders
}

# Copy stock pcmanfm desktop files when this user has none
seed_pi_desktop_conf() {
    # Leave existing wallpaper files alone
    shopt -s nullglob
    existing_conf=("${RUN_HOME}/.config/pcmanfm/"*/desktop-items-*.conf)
    shopt -u nullglob
    if [[ "${#existing_conf[@]}" -gt 0 ]]; then
        return
    fi

    # Prefer the system default profile from Raspberry Pi OS
    mkdir -p "${RUN_HOME}/.config/pcmanfm/default"
    if [[ -d "${PI_PCMANFM_DEFAULT}" ]]; then
        shopt -s nullglob
        stock_conf=("${PI_PCMANFM_DEFAULT}"/desktop-items-*.conf)
        shopt -u nullglob
        if [[ "${#stock_conf[@]}" -gt 0 ]]; then
            cp "${stock_conf[@]}" "${RUN_HOME}/.config/pcmanfm/default/"
            chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/pcmanfm/default/"desktop-items-*.conf
            return
        fi
    fi

    # Write a color-only desktop when the stock files are missing
    cat > "${RUN_HOME}/.config/pcmanfm/default/desktop-items-0.conf" <<EOF
[*]
wallpaper_mode=color
desktop_bg=${PI_DESKTOP_BG}
desktop_shadow=${PI_DESKTOP_BG}
show_documents=0
show_trash=0
show_mounts=0
EOF
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/pcmanfm/default/desktop-items-0.conf"
}

# Set or add one key in a pcmanfm desktop-items file
set_desktop_conf_key() {
    desktop_conf="$1"
    option_name="$2"
    option_value="$3"
    if grep -q "^${option_name}=" "${desktop_conf}"; then
        sed -i "s/^${option_name}=.*/${option_name}=${option_value}/" "${desktop_conf}"
        return
    fi
    printf '%s\n' "${option_name}=${option_value}" >> "${desktop_conf}"
}

# Load the Waveshare DSI panel overlay so the screen comes up at its native mode
configure_dsi_panel() {
    # Only the Pi boots from a firmware config and has a DSI connector
    if [[ "${MACHINE}" != "pi" || ! -f "${BOOT_CONFIG}" ]]; then
        return
    fi

    # Leave an overlay that is already there alone
    if grep -q "^dtoverlay=${PANEL_OVERLAY}$" "${BOOT_CONFIG}"; then
        echo "DSI panel overlay already set"
        return
    fi

    # Drop any other Waveshare panel overlay and our comment, the wrong variant leaves the screen dark
    echo "Adding DSI panel overlay to ${BOOT_CONFIG}"
    sed -i "\|^# ${PANEL_COMMENT}\$|d" "${BOOT_CONFIG}"
    sed -i '/^dtoverlay=vc4-kms-dsi-waveshare-panel/d' "${BOOT_CONFIG}"

    # Reuse a trailing all section so repeat runs do not stack empty ones
    if [[ "$(grep -vE '^[[:space:]]*$' "${BOOT_CONFIG}" | tail -1)" == "[all]" ]]; then
        printf '%s\n%s\n' "# ${PANEL_COMMENT}" "dtoverlay=${PANEL_OVERLAY}" >> "${BOOT_CONFIG}"
        return
    fi

    # Otherwise start a new all section, it applies the overlay on every Pi model
    printf '\n%s\n%s\n%s\n' '[all]' "# ${PANEL_COMMENT}" "dtoverlay=${PANEL_OVERLAY}" >> "${BOOT_CONFIG}"
}

# Enable GPIO 14/15 UART so the STS3215 servo bus has a port on Pi 5
configure_servo_uart() {
    # Only the Pi 5 overlay creates ttyAMA0 on the 40-pin header
    if [[ "${MACHINE}" != "pi" || ! -f "${BOOT_CONFIG}" ]]; then
        return
    fi

    # Leave an overlay that is already there alone
    if grep -q "^dtoverlay=${SERVO_UART_OVERLAY}$" "${BOOT_CONFIG}"; then
        echo "Servo UART overlay already set"
        return
    fi

    # Put it in the pi5 filter so other Pi models keep their own UART setup
    echo "Adding servo UART overlay to ${BOOT_CONFIG}"
    printf '\n%s\n%s\n%s\n' '[pi5]' "# ${SERVO_UART_COMMENT}" "dtoverlay=${SERVO_UART_OVERLAY}" >> "${BOOT_CONFIG}"
}

# Turn on the 40-pin I2C1 header so the INA219 battery meter can be read
configure_i2c_arm() {
    # Only the Pi boots the header I2C from this firmware key
    if [[ "${MACHINE}" != "pi" || ! -f "${BOOT_CONFIG}" ]]; then
        return
    fi

    # Leave an enabled line alone
    if grep -q "^dtparam=${I2C_ARM_PARAM}$" "${BOOT_CONFIG}"; then
        echo "I2C1 already enabled"
        return
    fi

    # Uncomment the stock line when it is still disabled
    echo "Enabling I2C1 for the battery meter"
    if grep -q "^#dtparam=${I2C_ARM_PARAM}" "${BOOT_CONFIG}"; then
        sed -i "s/^#dtparam=${I2C_ARM_PARAM}.*/dtparam=${I2C_ARM_PARAM}/" "${BOOT_CONFIG}"
        return
    fi

    # Otherwise append it
    printf '\n%s\n%s\n' "# ${I2C_ARM_COMMENT}" "dtparam=${I2C_ARM_PARAM}" >> "${BOOT_CONFIG}"
}

# Start X already in portrait and keep GNOME from flipping it back
persist_display_rotation() {
    if [[ "${MACHINE}" == "pi" ]]; then
        persist_pi_touch_rotation
        return
    fi
    echo "Keeping DP-1 rotated left from X start"

    # Ask the NVIDIA driver for left rotation on the first X modeset
    write_xorg_portrait

    # Same EDID as the 1024x600 Waveshare on DP-1, vendor ADA product 0x0004
    write_monitors_xml "${RUN_HOME}/.config/monitors.xml"
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/monitors.xml"

    # Login screen uses the same layout
    mkdir -p /var/lib/gdm3/.config
    write_monitors_xml /var/lib/gdm3/.config/monitors.xml
    chown gdm:gdm /var/lib/gdm3/.config/monitors.xml 2>/dev/null || true
}

# Draw nothing where the pointer is, the face should never show a cursor
hide_mouse_pointer() {
    echo "Hiding the mouse pointer"
    write_blank_cursor_theme
    select_blank_cursor_theme
    reload_compositor_config
}

# One transparent cursor, with every common pointer name pointing at it
write_blank_cursor_theme() {
    theme_dir="${RUN_HOME}/.icons/${CURSOR_THEME_NAME}"
    mkdir -p "${theme_dir}/cursors"
    cat > "${theme_dir}/index.theme" <<EOF
[Icon Theme]
Name=${CURSOR_THEME_NAME}
Comment=Transparent pointer for the robot face
EOF

    # Xcursor file holding a single fully transparent pixel, a fixed header then the image chunk
    printf '\x58\x63\x75\x72\x10\x00\x00\x00\x00\x00\x01\x00\x01\x00\x00\x00\x02\x00\xfd\xff\x18\x00\x00\x00\x1c\x00\x00\x00\x24\x00\x00\x00\x02\x00\xfd\xff\x18\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00' > "${theme_dir}/cursors/default"

    # Every shape the desktop might ask for resolves to the transparent one
    for cursor_name in ${CURSOR_POINTER_NAMES}; do
        ln -sf default "${theme_dir}/cursors/${cursor_name}"
    done
    chown -R "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.icons"
}

# labwc reads this file at login, and hands the theme to the apps it starts
select_blank_cursor_theme() {
    environment_file="${RUN_HOME}/.config/labwc/environment"
    mkdir -p "${RUN_HOME}/.config/labwc"
    touch "${environment_file}"
    sed -i '/^XCURSOR_THEME=/d' "${environment_file}"
    echo "XCURSOR_THEME=${CURSOR_THEME_NAME}" >> "${environment_file}"
    chown -R "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/labwc"
}

# Ask the running compositor to re-read its config, a fresh login picks it up anyway
reload_compositor_config() {
    pkill -HUP -u "${RUN_USER}" labwc >/dev/null 2>&1 || true
}

# Rotate the Waveshare DSI panel to portrait and keep touch on that output
persist_pi_touch_rotation() {
    echo "Rotating ${PANEL_OUTPUT} to portrait"
    write_kanshi_portrait
    apply_pi_panel_rotation
    write_pi_touch_map
    map_pi_touch || true
}

# labwc starts kanshi, this profile is what keeps DSI-2 rotated after login
write_kanshi_portrait() {
    mkdir -p "${RUN_HOME}/.config/kanshi"
    cat > "${RUN_HOME}/.config/kanshi/config" <<EOF
profile {
    output ${PANEL_OUTPUT} enable mode ${PANEL_MODE} transform ${PANEL_TRANSFORM}
}
EOF
    chown -R "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/kanshi"
}

# Rotate the live compositor now, then ask kanshi to reload
apply_pi_panel_rotation() {
    wayland_display=""
    for socket_name in wayland-0 wayland-1; do
        if [[ -S "/run/user/${RUN_UID}/${socket_name}" ]]; then
            wayland_display="${socket_name}"
            break
        fi
    done
    if [[ -n "${wayland_display}" ]]; then
        sudo -u "${RUN_USER}" env HOME="${RUN_HOME}" XDG_RUNTIME_DIR="/run/user/${RUN_UID}" WAYLAND_DISPLAY="${wayland_display}" wlr-randr --output "${PANEL_OUTPUT}" --transform "${PANEL_TRANSFORM}" >/dev/null 2>&1 || true
    fi
    pkill -HUP -u "${RUN_USER}" kanshi >/dev/null 2>&1 || true
}

# Map Goodix or ft5x06 onto DSI-2 at login, for an X session
write_pi_touch_map() {
    mkdir -p "${RUN_HOME}/.config/autostart"
    cat > "${RUN_HOME}/.config/autostart/deskman-map-touch.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Map DSI touch
Exec=sh -c 'xinput list --name-only | grep -E "ft5x06|Goodix" | while IFS= read -r name; do xinput map-to-output "\$name" ${PANEL_OUTPUT}; done'
X-GNOME-Autostart-enabled=true
EOF
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart/deskman-map-touch.desktop"
}

# Map every DSI touch device onto the panel
map_pi_touch() {
    export DISPLAY="${DISPLAY:-:0}"
    xinput list --name-only 2>/dev/null | { grep -E 'ft5x06|Goodix' || true; } | while IFS= read -r touch_name; do
        xinput map-to-output "${touch_name}" "${PANEL_OUTPUT}"
    done
}

# Add the rotated MetaModes to the Tegra device section
write_xorg_portrait() {
    local xorg_conf=/etc/X11/xorg.conf
    local backup_conf=/etc/X11/xorg.conf.deskman-bak
    if [[ ! -f "${xorg_conf}" ]]; then
        echo "No ${xorg_conf}, skip X portrait options" >&2
        return
    fi
    if [[ ! -f "${backup_conf}" ]]; then
        cp "${xorg_conf}" "${backup_conf}"
    fi

    # Drop the old Monitor Rotate snippet, it fought GNOME and blanked the panel
    rm -f /etc/X11/xorg.conf.d/10-deskman-rotate.conf

    # Leave the file alone when the options are already present
    if grep -q 'Option.*"MetaModes".*Rotation=left' "${xorg_conf}"; then
        return
    fi

    # Insert the rotation option into the Tegra0 device section
    python3 - "${xorg_conf}" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
if 'Rotation=left' in text:
    raise SystemExit(0)
old = '''Section "Device"
    Identifier  "Tegra0"
    Driver      "nvidia"
# Allow X server to be started even if no display devices are connected.
    Option      "AllowEmptyInitialConfiguration" "true"
EndSection'''
new = '''Section "Device"
    Identifier  "Tegra0"
    Driver      "nvidia"
# Allow X server to be started even if no display devices are connected.
    Option      "AllowEmptyInitialConfiguration" "true"
    Option      "MetaModes" "DP-1: 1024x600 +0+0 {Rotation=left}"
EndSection'''
if old not in text:
    raise SystemExit('xorg.conf Device section is not the stock Tegra block')
open(path, 'w').write(text.replace(old, new, 1))
PY
}

# Write GNOME's monitor layout, the rate must match the mode to about 0.001Hz or mutter drops the whole config and falls back to landscape
write_monitors_xml() {
    local monitors_xml="$1"
    mkdir -p "$(dirname "${monitors_xml}")"
    cat > "${monitors_xml}" <<'EOF'
<monitors version="2">
  <configuration>
    <logicalmonitor>
      <x>0</x>
      <y>0</y>
      <scale>1</scale>
      <primary>yes</primary>
      <transform>
        <rotation>left</rotation>
        <flipped>no</flipped>
      </transform>
      <monitor>
        <monitorspec>
          <connector>DP-1</connector>
          <vendor>ADA</vendor>
          <product>0x0004</product>
          <serial>0x00000001</serial>
        </monitorspec>
        <mode>
          <width>1024</width>
          <height>600</height>
          <rate>59.851860046386719</rate>
        </mode>
      </monitor>
    </logicalmonitor>
  </configuration>
</monitors>
EOF
}

# Drop leftover Desktop launchers, keep Start Robot and Start Teleport
remove_extra_desktop_launchers() {
    extra_desktops=()
    for desktop_file in "${RUN_HOME}/Desktop/"*.desktop; do

        # Skip a missing glob when Desktop has no launchers
        if [[ ! -e "${desktop_file}" ]]; then
            continue
        fi

        # Keep the Start Robot and Start Teleport icons
        base_name="$(basename "${desktop_file}")"
        if [[ "${base_name}" == "robot.desktop" || "${base_name}" == "teleport.desktop" ]]; then
            continue
        fi
        extra_desktops+=("${desktop_file}")
    done

    # Nothing else to remove
    if [[ "${#extra_desktops[@]}" -eq 0 ]]; then
        return
    fi
    confirm_rm "${extra_desktops[@]}"
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

# Desktop icons to start robot.service and teleport.service without a password
enable_service_shortcuts() {
    echo "Adding Robot and Teleport desktop items"

    # Allow this user to start those two units from the icons, and restart them by voice
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    install -m 0755 "${script_dir}/restart_services.sh" /usr/local/bin/deskman-restart-services
    sudoers_file="/etc/sudoers.d/deskman-services"
    printf '%s\n' "${RUN_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl start robot.service, /usr/bin/systemctl start teleport.service, /usr/local/bin/deskman-restart-services" > "${sudoers_file}"
    chmod 0440 "${sudoers_file}"

    # Add the launchers when they are missing
    mkdir -p "${RUN_HOME}/Desktop"
    install_robot_eyes_icon
    write_service_shortcut "Start Robot" robot "${RUN_HOME}/.local/share/icons/deskman-robot.svg" "${script_dir}/start_robot.sh"
    write_service_shortcut "Start Teleport" teleport camera-web "sudo -n /usr/bin/systemctl start teleport.service"
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
    exec_command="$4"
    desktop_file="${RUN_HOME}/Desktop/${service_name}.desktop"

    # Write a trusted launcher the desktop will run on tap, replacing an older one
    cat > "${desktop_file}" <<EOF
[Desktop Entry]
Type=Application
Name=${launcher_name}
Comment=Start ${service_name}.service
Exec=${exec_command}
Icon=${icon_name}
Terminal=false
Categories=Utility;
EOF
    chown "${RUN_USER}:${RUN_USER}" "${desktop_file}"
    chmod 0755 "${desktop_file}"
    run_as_user gio set "${desktop_file}" metadata::trusted true || true
}

# Give the panel a touch keyboard, GNOME uses onboard and the Pi uses matchbox
enable_screen_keyboard() {
    echo "Enabling the on-screen keyboard"

    # LXDE has no keyboard of its own, so install one with a taskbar button
    if [[ "${MACHINE}" == "pi" ]]; then
        install_touch_keyboard
        install_clipboard_keeper
        return
    fi

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

# Matchbox keyboard with our layout, docked along the bottom, shown from a taskbar button
install_touch_keyboard() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y matchbox-keyboard

    # Our layout, digits and web punctuation over the stock letters
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    install -m 0644 "${script_dir}/keyboard/keyboard-deskman.xml" "${MATCHBOX_LAYOUT_DIR}/keyboard-deskman.xml"

    # Dock it, keep it above the browser, and leave typing focus with the text field
    add_openbox_keyboard_rule

    # Taskbar button that shows and hides it
    write_keyboard_launcher
    add_panel_keyboard_button
}

# Openbox rule that places the keyboard window, matched on its title as it sets no class
add_openbox_keyboard_rule() {
    openbox_config="${RUN_HOME}/.config/openbox/${OPENBOX_CONFIG_NAME}"

    # Start from the session defaults when the user has no file of their own
    if [[ ! -f "${openbox_config}" ]]; then
        mkdir -p "$(dirname "${openbox_config}")"
        cp "/etc/xdg/openbox/${OPENBOX_CONFIG_NAME}" "${openbox_config}"
    fi

    # Leave it alone once the rule is in
    if grep -q 'title="Keyboard"' "${openbox_config}"; then
        return
    fi

    # Openbox keeps one applications section, so join it or add one
    if grep -q '<applications>' "${openbox_config}"; then
        sed -i "s|<applications>|<applications>${OPENBOX_KEYBOARD_RULE}|" "${openbox_config}"
    else
        sed -i "s|</openbox_config>|<applications>${OPENBOX_KEYBOARD_RULE}</applications></openbox_config>|" "${openbox_config}"
    fi
    chown "${RUN_USER}:${RUN_USER}" "${openbox_config}"
}

# Launcher the taskbar button runs, it shows the keyboard or takes it away
write_keyboard_launcher() {
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    launcher_dir="${RUN_HOME}/.local/share/applications"
    mkdir -p "${launcher_dir}"

    # Write it every time so a moved repo still gets a working button
    cat > "${launcher_dir}/${KEYBOARD_LAUNCHER_NAME}" <<EOF
[Desktop Entry]
Type=Application
Name=Keyboard
Comment=Show or hide the touch keyboard
Exec=${script_dir}/toggle_keyboard.sh
Icon=input-keyboard
Terminal=false
Categories=Utility;
EOF
    chown -R "${RUN_USER}:${RUN_USER}" "${launcher_dir}"
}

# Put the keyboard button in the taskbar launch bar, next to the browser and terminal
add_panel_keyboard_button() {
    panel_config="${RUN_HOME}/.config/lxpanel/LXDE-pi/panels/panel"

    # Nothing to edit without a panel config, and nothing to do once the button is there
    if [[ ! -f "${panel_config}" ]] || grep -q "${KEYBOARD_LAUNCHER_NAME}" "${panel_config}"; then
        return
    fi

    # Add the button as the first one in the launch bar
    awk -v launcher="${KEYBOARD_LAUNCHER_NAME}" '{ print } /type=launchbar/ { getline config_line; print config_line; print "    Button {"; print "      id=" launcher; print "    }" }' "${panel_config}" > "${panel_config}.new"
    mv "${panel_config}.new" "${panel_config}"
    chown "${RUN_USER}:${RUN_USER}" "${panel_config}"

    # Reload the panel so the button appears without a logout
    run_as_user lxpanelctl restart || true
}

# Hold on to what was copied, X11 loses the clipboard as soon as the browser closes
install_clipboard_keeper() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y parcellite

    # Parcellite starts itself at login, so only its own settings are left to write
    clipboard_config="${RUN_HOME}/.config/parcellite/parcelliterc"
    if [[ -f "${clipboard_config}" ]]; then
        return
    fi

    # Keep the history in memory only, copied API keys have no business on disk
    mkdir -p "$(dirname "${clipboard_config}")"
    printf '%s\n' '[rc]' 'save_history=false' > "${clipboard_config}"
    chown -R "${RUN_USER}:${RUN_USER}" "$(dirname "${clipboard_config}")"
}

# Keep the display on and skip the lock screen
disable_screen_idle() {
    echo "Disabling screen blanking and screensaver"

    # GNOME idle and lock, Jetson only
    if [[ "${MACHINE}" == "jetson" ]]; then
        disable_gnome_screen_idle
    fi

    # Ignore logind idle so the session stays logged in
    mkdir -p /etc/systemd/logind.conf.d
    cat > /etc/systemd/logind.conf.d/disable-idle.conf <<'EOF'
[Login]
IdleAction=ignore
EOF

    # Turn off X screensaver and DPMS at login
    mkdir -p "${RUN_HOME}/.config/autostart"
    cat > "${RUN_HOME}/.config/autostart/disable-screen-blank.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Disable screen blank
Exec=sh -c "xset s off; xset s noblank; xset -dpms"
X-GNOME-Autostart-enabled=true
EOF
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart/disable-screen-blank.desktop"
}

# Never idle into the GNOME screensaver or lock
disable_gnome_screen_idle() {
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
}

# Publish this machine as hostname.local, Avahi renames itself when IPv6 addresses come and go
# Stop Wi-Fi power save, it parks the radio between beacons and drops incoming call media
keep_wifi_awake() {
    # Tell NetworkManager to leave the radio on for every Wi-Fi connection
    mkdir -p "${NETWORK_MANAGER_CONF_DIR}"
    cat > "${NETWORK_MANAGER_CONF_DIR}/${WIFI_POWER_SAVE_CONF_NAME}" <<EOF
# Written by robot/install_system.sh, keeps call audio and video flowing
[connection]
wifi.powersave = 2
EOF
    echo "Wrote ${NETWORK_MANAGER_CONF_DIR}/${WIFI_POWER_SAVE_CONF_NAME}"

    # Turn it off on the radios that are already up, so this call works without a reboot
    for wifi_device in /sys/class/net/*/wireless; do
        [[ -e "${wifi_device}" ]] || continue
        interface="$(basename "$(dirname "${wifi_device}")")"
        iw dev "${interface}" set power_save off >/dev/null 2>&1 || true
        echo "Wi-Fi power save off on ${interface}"
    done

    # Reload so the setting sticks without waiting for the next boot
    if systemctl is-active --quiet NetworkManager; then
        systemctl reload NetworkManager || true
    fi
}

fix_mdns_name() {
    if [[ ! -f "${AVAHI_CONF}" ]]; then
        echo "No Avahi config, skip mDNS"
        return
    fi
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
    allow_interfaces="${AVAHI_ALLOW_INTERFACES}"
    if [[ "${MACHINE}" == "pi" ]]; then
        allow_interfaces="$(list_mdns_interfaces)"
    fi
    if [[ -n "${allow_interfaces}" ]]; then
        set_avahi_option allow-interfaces "${allow_interfaces}"
    fi
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
    hide_autostart nvpmodel_indicator.desktop

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

    # Make the folder even when prepare_user_dirs did not run
    mkdir -p "${RUN_HOME}/.config/autostart"
    chown "${RUN_USER}:${RUN_USER}" "${RUN_HOME}/.config/autostart"

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

# Wi-Fi and ethernet names on this board, skip lo, docker, and USB gadget
list_mdns_interfaces() {
    ip -o link show | awk -F': ' '{print $2}' | awk -F'@' '{print $1}' | { grep -E '^(wlan|wl|eth|enP|enx|eno)' || true; } | paste -sd, -
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
