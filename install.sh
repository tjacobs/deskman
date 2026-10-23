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

    # Install talk voice interaction
    "${ROOT_DIR}/talk/install.sh"

    # Install teleport video calls
    "${ROOT_DIR}/teleport/install.sh"

    # Install robot service
    "${ROOT_DIR}/robot/install_robot_service.sh"

    # Install teleport service
    "${ROOT_DIR}/teleport/install_teleport_service.sh"

    # Remove unused apps
    remove_unused_apps

    # Delete caches we no longer need
    free_disk_space
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
    # Remove desktop apps, then the extra CUDA, firmware, and images
    remove_desktop_packages
    remove_cuda_static_libraries
    remove_unused_firmware
    remove_ota_payloads
    remove_triton
    remove_qemu_image
    remove_cuda_gdb
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

# Delete CUDA static archives, the shared libraries stay for the compiler and the builds
remove_cuda_static_libraries() {
    local library
    local libraries=()

    # Collect static archives, keep the runtime one nvcc needs to identify the compiler
    shopt -s nullglob
    for library in /usr/local/cuda-*/targets/*/lib/*_static.a; do
        if [[ "$(basename "${library}")" == "libcudart_static.a" ]]; then
            continue
        fi
        libraries+=("${library}")
    done
    shopt -u nullglob

    # Nothing to remove
    if [[ ${#libraries[@]} -eq 0 ]]; then
        return 0
    fi

    # Delete the archives
    echo "Removing CUDA static libraries."
    sudo rm -f "${libraries[@]}"
}

# Delete firmware for devices this board does not have
remove_unused_firmware() {
    local directory
    local name
    local directories=()

    # Skip NVIDIA and Realtek, this board uses those
    shopt -s nullglob
    for directory in /usr/lib/firmware/*/; do
        name="$(basename "${directory}")"
        case "${name}" in
            nvidia|rtw88|rtl_nic|rtl_bt|realtek)
                continue
                ;;
        esac
        directories+=("${directory}")
    done
    shopt -u nullglob

    # Nothing to remove
    if [[ ${#directories[@]} -eq 0 ]]; then
        return 0
    fi

    # Delete the other device firmware
    echo "Removing firmware for other devices."
    sudo rm -rf "${directories[@]}"
}

# Delete stored Jetson flash images, the bootloader package itself stays
remove_ota_payloads() {
    # Nothing to remove
    if [[ ! -d "${OTA_PACKAGE_DIR}" ]]; then
        return 0
    fi

    # Delete the image files and leave the package folders
    echo "Removing Jetson OTA flash images."
    sudo find "${OTA_PACKAGE_DIR}" -type f -delete
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

# Delete the CUDA debugger
remove_cuda_gdb() {
    local status
    local package
    local packages=()

    # Collect installed debugger packages
    while read -r status package; do
        if [[ "${status}" != "ii" ]]; then
            continue
        fi
        packages+=("${package}")
    done < <(dpkg-query -W -f '${db:Status-Abbrev} ${Package}\n' 'cuda-gdb*' 2>/dev/null || true)

    # Nothing to remove
    if [[ ${#packages[@]} -eq 0 ]]; then
        return 0
    fi

    # Remove the debugger
    echo "Removing the CUDA debugger."
    apt_get purge -y "${packages[@]}"
    clean_apt_cache
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

# Run apt-get as root without prompts or the no longer required list
apt_get() {
    sudo env "${APT_ENVIRONMENT[@]}" apt-get -o APT::Get::HideAutoRemove=1 "$@"
}

# Return how many parallel compile jobs fit in memory, at least one and at most the CPU count
build_jobs() {
    local cpu_count memory_gigabytes jobs
    cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"

    # Use every cpu where memory is not readable, as on mac
    if [[ ! -r /proc/meminfo ]]; then
        echo "${cpu_count}"
        return 0
    fi

    # Limit jobs so compilers do not push a small robot into swap
    memory_gigabytes="$(awk '/^MemTotal:/ {print int($2 / 1048576)}' /proc/meminfo)"
    jobs=$(( memory_gigabytes / MEMORY_PER_BUILD_JOB_GB ))
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

# Config build parallelism, each compile job can use this much memory
MEMORY_PER_BUILD_JOB_GB=2

# Config apt so nothing stops to ask a question
APT_ENVIRONMENT=(DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a)
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

# Config the places that hold caches an install no longer needs
UV_BIN_DIR="${HOME}/.local/bin"
PIP_CACHE_DIR="${HOME}/.cache/pip"

# Config stored Jetson flash images, owned by the bootloader package
OTA_PACKAGE_DIR="/opt/ota_package"

# Run install only when executed, sourcing this file just loads the functions
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # Stop on errors
    set -euo pipefail
    main "$@"
fi

