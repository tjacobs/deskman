#!/usr/bin/env bash
# Set Jetson power mode: min, mid, or max.
# Usage: ./tools/power.sh
#        ./tools/power.sh min
#        ./tools/power.sh mid
#        ./tools/power.sh max

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config power model ids from /etc/nvpmodel.conf
MODE_MIN=0
MODE_MID=1
MODE_MAX=2
NAME_MIN="15W"
NAME_MID="25W"
NAME_MAX="25W"

# Re-exec with sudo if not root, help does not need it
if [[ "${EUID}" -ne 0 && "${1:-}" != "-h" && "${1:-}" != "--help" ]]; then
    exec sudo --preserve-env=SUDO_USER bash "${BASH_SOURCE[0]}" "$@"
fi

# Main
main() {
    # Parse args, show status when none given
    parse_args "$@"
    if [[ -z "${POWER_MODE}" ]]; then
        show_status
        return 0
    fi

    # Apply the selected power mode
    apply_power_mode "${POWER_MODE}"
    show_status
}

# Parse command line arguments
parse_args() {
    POWER_MODE=""

    # Read each argument
    for argument in "$@"; do
        case "${argument}" in
            min)
                POWER_MODE="min"
                ;;
            mid)
                POWER_MODE="mid"
                ;;
            max)
                POWER_MODE="max"
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                echo "Unknown argument: ${argument}"
                print_usage
                exit 1
                ;;
        esac
    done
}

# Print usage help
print_usage() {
    echo "Usage: ./tools/power.sh [min|mid|max]"
    echo "  min       ${NAME_MIN}, quietest and coolest, clocks scale with load"
    echo "  mid       ${NAME_MID}, balanced"
    echo "  max       ${NAME_MAX}, full performance, clocks locked high"
    echo "  (no arg)  show current power status"
}

# Apply min, mid, or max power settings
apply_power_mode() {
    local mode="$1"

    # Set nvpmodel and jetson_clocks for the chosen profile
    case "${mode}" in
        min)
            nvpmodel -m "${MODE_MIN}"
            disable_jetson_clocks
            echo "Set power mode: min (${NAME_MIN})"
            ;;
        mid)
            nvpmodel -m "${MODE_MID}"
            disable_jetson_clocks
            echo "Set power mode: mid (${NAME_MID})"
            ;;
        max)
            nvpmodel -m "${MODE_MAX}"
            enable_jetson_clocks
            echo "Set power mode: max (${NAME_MAX})"
            ;;
    esac
}

# Stop jetson_clocks so clocks can scale with load
disable_jetson_clocks() {
    systemctl stop jetson_clocks 2>/dev/null || true
    systemctl disable jetson_clocks 2>/dev/null || true
    if command -v jetson_clocks >/dev/null 2>&1; then
        jetson_clocks --restore 2>/dev/null || true
    fi
}

# Lock clocks high for max performance
enable_jetson_clocks() {
    systemctl enable jetson_clocks 2>/dev/null || true
    systemctl start jetson_clocks 2>/dev/null || true
    if command -v jetson_clocks >/dev/null 2>&1; then
        jetson_clocks 2>/dev/null || true
    fi
}

# Print current power mode, clocks, temp, and fan
show_status() {
    local mode_name mode_id mode_label min_freq max_freq cur_freq cpu_temp fan_rpm

    # Read nvpmodel and map to min mid max
    mode_name="$(nvpmodel -q 2>/dev/null | awk -F': ' '/NV Power Mode/{print $2; exit}')"
    mode_id="$(nvpmodel -q 2>/dev/null | awk '/^[0-9]+$/{print; exit}')"
    case "${mode_id}" in
        "${MODE_MIN}") mode_label="min ${NAME_MIN}" ;;
        "${MODE_MID}") mode_label="mid ${NAME_MID}" ;;
        "${MODE_MAX}") mode_label="max ${NAME_MAX}" ;;
        *) mode_label="${mode_name} (id ${mode_id})" ;;
    esac

    # Read CPU freqs in MHz
    min_freq="$(awk '{printf "%.0f", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq)"
    max_freq="$(awk '{printf "%.0f", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq)"
    cur_freq="$(awk '{printf "%.0f", $1/1000}' /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq)"

    # Read CPU temp and fan rpm when available
    cpu_temp="$(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo '?')"
    fan_rpm="$(cat /sys/class/hwmon/hwmon2/rpm 2>/dev/null || echo '?')"

    echo "Power: ${mode_label}"
    echo "CPU: ${cur_freq} MHz, range ${min_freq}-${max_freq} MHz, $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
    echo "Temp: ${cpu_temp}°C"
    echo "Fan: ${fan_rpm} RPM"
}

main "$@"
