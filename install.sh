#!/usr/bin/env bash
# Install robot, talk, and teleport, the robot and teleport services and system setup.

# Main
main() {
    # Check the install fits
    check_disk_space

    # Ask for the sudo password once
    prime_sudo

    # Configure the system first, it keeps Wi-Fi awake and fixes the hostname.local name
    "${ROOT_DIR}/robot/install_system.sh"

    # Install robot face
    "${ROOT_DIR}/robot/install.sh"

    # Install robot service
    "${ROOT_DIR}/robot/install_robot_service.sh"

    # Install talk voice interaction
    "${ROOT_DIR}/talk/install.sh"

    # Install teleport video calls
    "${ROOT_DIR}/teleport/install.sh"

    # Install teleport service
    "${ROOT_DIR}/teleport/install_teleport_service.sh"

    # Delete caches we no longer need
    free_disk_space

    # Say the whole install finished
    echo "All done."
}

# Stop before any work when the disk is short, unless the user types y to continue
check_disk_space() {
    # Say what is being checked
    echo "Checking disk space..."

    # Pass when there is enough room
    local available_gigabytes
    available_gigabytes="$(free_gigabytes)"
    if (( available_gigabytes >= REQUIRED_DISK_GB )); then
        echo "Disk space OK, ${available_gigabytes} GB free, need ${REQUIRED_DISK_GB} GB."
        return 0
    fi

    # Ask on a terminal, Enter keeps the default and stops
    local answer
    echo "Not enough disk space, need ${REQUIRED_DISK_GB} GB free and have ${available_gigabytes} GB."
    if [[ -t 0 ]]; then
        read -r -p "Continue with low disk space? [y/N] " answer
        if [[ "${answer}" == "y" || "${answer}" == "Y" ]]; then
            return 0
        fi
    fi
    echo "Delete files or use bigger storage, then run again."
    exit 1
}

# Return the whole gigabytes free on the install disk
free_gigabytes() {
    df -Pk "${DISK_CHECK_PATH}" | awk -v divisor="${KILOBYTES_PER_GIGABYTE}" 'NR==2 {print int($4 / divisor)}'
}

# Ask for the sudo password once up front and keep it fresh until the install ends
prime_sudo() {
    # Skip on mac and when already root
    if [[ "$(uname -s)" == "Darwin" || "$(id -u)" -eq 0 ]]; then
        return 0
    fi

    # Ask once when sudo has no password cached, this needs a terminal
    if ! sudo -n true 2>/dev/null; then
        if [[ ! -t 0 ]]; then
            echo "sudo needs a password and there is no terminal to ask on. Run from a terminal, over ssh use ssh -t."
            exit 1
        fi
        echo "Enter your password once, the install then runs without asking again."
        sudo -v
    fi

    # Keep the password fresh in the background, one keeper for the whole install
    if [[ -z "${DESKMAN_SUDO_KEEPER:-}" ]]; then
        export DESKMAN_SUDO_KEEPER=1
        keep_sudo_fresh >/dev/null 2>&1 </dev/null &
        SUDO_KEEPER_PID=$!
        trap 'kill "${SUDO_KEEPER_PID}" 2>/dev/null || true' EXIT
    fi
}

# Refresh the sudo timestamp until the install script exits
keep_sudo_fresh() {
    while kill -0 "$$" 2>/dev/null; do
        sudo -n -v 2>/dev/null || true
        sleep "${SUDO_REFRESH_SECONDS}"
    done
}

# Remove profilers, desktop apps, and other installs the robot does not run
remove_unused_apps() {
    # Remove desktop apps, then the extra images
    remove_desktop_packages
    remove_triton
    remove_qemu_image
}

# Remove Nsight and the desktop apps the robot does not run
remove_desktop_packages() {
    local package
    local packages=()

    # Collect profilers and office apps that are installed
    while read -r package; do
        packages+=("${package}")
    done < <(dpkg-query -W -f '${Package}\n' | grep -E '^(firefox|thunderbird|libreoffice-|nsight-compute|nsight-systems|cuda-nsight-compute|cuda-nsight-systems)' || true)

    # Nothing to remove
    if [[ ${#packages[@]} -eq 0 ]]; then
        return 0
    fi

    # Keep the CUDA libraries, the toolkit metapackage leaves with Nsight
    keep_cuda_packages

    # Remove the profilers and desktop apps, then drop the downloaded package files
    echo "Removing Nsight, Firefox, Thunderbird, and LibreOffice."
    apt_get purge -y "${packages[@]}"
    clean_apt_cache
}

# Mark the CUDA compiler and libraries manual so they stay when the toolkit metapackage is removed
keep_cuda_packages() {
    local package
    local packages=()

    # Skip Nsight itself, everything else under these names is the compiler and libraries
    while read -r package; do
        case "${package}" in
            cuda-nsight-*|nsight-*)
                continue
                ;;
        esac
        packages+=("${package}")
    done < <(dpkg-query -W -f '${Package}\n' 'cuda-*' 'libcublas*' 'libcufft*' 'libcusparse*' 'libcusolver*' 'libcudnn*' 'libcurand*' 'libnpp*' 'nvidia-cuda*' 2>/dev/null || true)

    # Nothing to mark
    if [[ ${#packages[@]} -eq 0 ]]; then
        return 0
    fi
    sudo apt-mark manual "${packages[@]}" >/dev/null
}

# Delete Triton from the talk venv, speech does not compile kernels
remove_triton() {
    local path
    local paths=()

    # Collect the package and its install metadata
    shopt -s nullglob
    for path in "${ROOT_DIR}/talk/.venv/lib/python"*/site-packages/triton "${ROOT_DIR}/talk/.venv/lib/python"*/site-packages/triton-*.dist-info; do
        paths+=("${path}")
    done
    shopt -u nullglob

    # Nothing to remove
    if [[ ${#paths[@]} -eq 0 ]]; then
        return 0
    fi

    # Delete the package
    echo "Removing Triton."
    rm -rf "${paths[@]}"
}

# Delete the QEMU UEFI image, the robot does not run virtual machines
remove_qemu_image() {
    # Nothing to remove
    if ! dpkg -s qemu-efi-aarch64 >/dev/null 2>&1; then
        return 0
    fi

    # Remove the image package
    echo "Removing the QEMU UEFI image."
    apt_get purge -y qemu-efi-aarch64
}

# Delete caches an install leaves behind and report the space that is free
free_disk_space() {
    local before_gigabytes
    before_gigabytes="$(free_gigabytes)"

    # Remove downloaded packages, pip wheels, and the uv cache, installed files stay
    clean_apt_cache
    rm -rf "${PIP_CACHE_DIR}"
    if command -v uv >/dev/null 2>&1 || [[ -x "${UV_BIN_DIR}/uv" ]]; then
        PATH="${UV_BIN_DIR}:${PATH}" uv cache clean >/dev/null 2>&1 || true
    fi
    echo "Cleaned caches, free space went from ${before_gigabytes} GB to $(free_gigabytes) GB."
}

# Delete downloaded package files apt keeps after installing
clean_apt_cache() {
    if command -v apt-get >/dev/null 2>&1; then
        apt_get clean
    fi
}

# Run apt-get as root without prompts or the no longer required list, one line per package
apt_get() {
    # Keep the Unpacking line and drop the other dpkg steps, errors still show
    sudo env "${APT_ENVIRONMENT[@]}" apt-get -qq -o APT::Get::HideAutoRemove=1 "$@" 2>&1 | awk '!/^(Selecting previously unselected package|Preparing to unpack|Setting up|Processing triggers|\(Reading database)/ { print; fflush() }'

    # Hand back apt-get's own exit code, not the filter's
    return "${PIPESTATUS[0]}"
}

# Return how many parallel compile jobs fit in memory, at least one and at most the CPU count
build_jobs() {
    local cpu_count memory_megabytes jobs
    cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"

    # Use every cpu where memory is not readable, as on mac
    if [[ ! -r /proc/meminfo ]]; then
        echo "${cpu_count}"
        return 0
    fi

    # Limit jobs from total memory, swap can be compressed ram so free memory misleads
    memory_megabytes="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
    jobs=$(( memory_megabytes / MEMORY_PER_BUILD_JOB_MB ))
    if (( jobs > cpu_count )); then
        jobs="${cpu_count}"
    fi
    if (( jobs < 1 )); then
        jobs=1
    fi
    echo "${jobs}"
}

# Repo root, so the script works from any directory
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Config sudo, keep the password fresh while the install runs
SUDO_REFRESH_SECONDS=60

# Config disk checks, the path whose free space is measured
DISK_CHECK_PATH="/"
KILOBYTES_PER_GIGABYTE=1048576

# Config the free disk space in gigabytes the install needs
REQUIRED_DISK_GB=10

# Config build parallelism, total memory per compile job, one near 570 MB at peak plus the system's share, so 2 GB runs 2 and 4 GB runs 4
MEMORY_PER_BUILD_JOB_MB=950

# Config apt so nothing stops to ask a question
APT_ENVIRONMENT=(DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a)
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

# Config the places that hold caches an install no longer needs
UV_BIN_DIR="${HOME}/.local/bin"
PIP_CACHE_DIR="${HOME}/.cache/pip"

# Config the file L4T ships on a Jetson, other boards do not have it
JETSON_RELEASE_FILE="/etc/nv_tegra_release"

# Run install only when executed, sourcing this file just loads the functions
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Stop on errors
    set -euo pipefail
    main "$@"
fi

