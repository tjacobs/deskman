#!/usr/bin/env bash
# Fake a USB soundcard so speak.py, say.py, and test.py run where there is no audio hardware.
# Usage: ./tools-fake-soundcard.sh
#        ./tools-fake-soundcard.sh --test
#        ./tools-fake-soundcard.sh --speak

# Exit on error, undefined variables, and pipe failure
set -euo pipefail

# Config paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${SCRIPT_DIR}/cache"
SHIM_SOURCE="${CACHE_DIR}/fake_soundcard.c"
SHIM_LIBRARY="${CACHE_DIR}/fake_soundcard.so"
FAKE_CARDS="${CACHE_DIR}/asound_cards"
ASOUND_CONFIG="${HOME}/.asoundrc"

# Config the fake card line, find_usb_card looks for a USB-Audio entry in /proc/asound/cards
FAKE_CARDS_TEXT=' 0 [FakeUSB        ]: USB-Audio - Fake USB Audio
                      Fake USB Audio Device'

# State
MODE="setup"

# Main
main() {
    parse_args "$@"
    check_tools
    build_shim
    write_fake_cards
    write_asound_config

    # Run a demo when asked, else print how to use the shim
    if [[ "${MODE}" == "test" ]]; then
        run_test
    elif [[ "${MODE}" == "speak" ]]; then
        run_speak
    else
        print_ready
    fi
}

# Parse command line arguments
parse_args() {
    for argument in "$@"; do
        case "${argument}" in
            --test)
                MODE="test"
                ;;
            --speak)
                MODE="speak"
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
    echo "Usage: ./tools-fake-soundcard.sh [--test] [--speak]"
    echo "  --test    set up the fake soundcard then run ./test.py under it"
    echo "  --speak   set up the fake soundcard then run ./speak.py under it"
    echo "  (no arg)  set up the fake soundcard and print the LD_PRELOAD line to use"
}

# Quit when the compiler is missing
check_tools() {
    if ! command -v gcc >/dev/null 2>&1; then
        echo "gcc not found. Install with: sudo apt install gcc"
        exit 1
    fi
}

# Build the preload shim that redirects reads of /proc/asound/cards to the fake file
build_shim() {
    mkdir -p "${CACHE_DIR}"
    write_shim_source
    gcc -shared -fPIC -O2 -o "${SHIM_LIBRARY}" "${SHIM_SOURCE}" -ldl
}

# Write the fake cards file the shim points at
write_fake_cards() {
    printf '%s\n' "${FAKE_CARDS_TEXT}" > "${FAKE_CARDS}"
}

# Write a null ALSA default so aplay plays to nowhere without hardware
write_asound_config() {
    # Keep an existing config, only create one when missing
    if [[ -f "${ASOUND_CONFIG}" ]]; then
        echo "Keeping existing ${ASOUND_CONFIG}, ensure its default pcm is type null for silent playback."
        return 0
    fi
    printf 'pcm.!default { type null }\nctl.!default { type null }\n' > "${ASOUND_CONFIG}"
}

# Run the test suite under the shim
run_test() {
    LD_PRELOAD="${SHIM_LIBRARY}" "${SCRIPT_DIR}/test.py"
}

# Run speak.py under the shim
run_speak() {
    LD_PRELOAD="${SHIM_LIBRARY}" "${SCRIPT_DIR}/speak.py"
}

# Print how to use the shim in this shell
print_ready() {
    echo "Fake soundcard ready."
    echo "Run tools that play audio with the shim preloaded:"
    echo "  LD_PRELOAD=${SHIM_LIBRARY} ./speak.py"
    echo "  LD_PRELOAD=${SHIM_LIBRARY} ./test.py"
    echo "Or export it for this shell:"
    echo "  export LD_PRELOAD=${SHIM_LIBRARY}"
}

# Write the C source for the preload shim
write_shim_source() {
    cat > "${SHIM_SOURCE}" <<SHIM
#define _GNU_SOURCE
#include <dlfcn.h>
#include <string.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>

/* Redirect reads and stats of the ALSA cards file to a fake we control */
static const char *TARGET = "/proc/asound/cards";
static const char *FAKE = "${FAKE_CARDS}";

/* Swap the target path for the fake path */
static const char *redirect(const char *path) {
    if (path && strcmp(path, TARGET) == 0) return FAKE;
    return path;
}

/* Intercept open */
int open(const char *path, int flags, ...) {
    static int (*real)(const char *, int, ...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "open");
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    return real(redirect(path), flags, mode);
}

/* Intercept open64 */
int open64(const char *path, int flags, ...) {
    static int (*real)(const char *, int, ...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "open64");
    mode_t mode = 0;
    if (flags & O_CREAT) { va_list ap; va_start(ap, flags); mode = va_arg(ap, mode_t); va_end(ap); }
    return real(redirect(path), flags, mode);
}

/* Intercept fopen */
FILE *fopen(const char *path, const char *mode) {
    static FILE *(*real)(const char *, const char *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fopen");
    return real(redirect(path), mode);
}

/* Intercept fopen64 */
FILE *fopen64(const char *path, const char *mode) {
    static FILE *(*real)(const char *, const char *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fopen64");
    return real(redirect(path), mode);
}

/* Intercept stat */
int stat(const char *path, struct stat *buf) {
    static int (*real)(const char *, struct stat *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "stat");
    return real(redirect(path), buf);
}

/* Intercept stat64 */
int stat64(const char *path, struct stat64 *buf) {
    static int (*real)(const char *, struct stat64 *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "stat64");
    return real(redirect(path), buf);
}

/* Intercept lstat */
int lstat(const char *path, struct stat *buf) {
    static int (*real)(const char *, struct stat *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "lstat");
    return real(redirect(path), buf);
}

/* Intercept the versioned xstat */
int __xstat(int ver, const char *path, struct stat *buf) {
    static int (*real)(int, const char *, struct stat *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "__xstat");
    return real(ver, redirect(path), buf);
}

/* Intercept the versioned xstat64 */
int __xstat64(int ver, const char *path, struct stat64 *buf) {
    static int (*real)(int, const char *, struct stat64 *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "__xstat64");
    return real(ver, redirect(path), buf);
}

/* Intercept fstatat */
int fstatat(int dirfd, const char *path, struct stat *buf, int flags) {
    static int (*real)(int, const char *, struct stat *, int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fstatat");
    return real(dirfd, redirect(path), buf, flags);
}

/* Intercept the versioned fxstatat */
int __fxstatat(int ver, int dirfd, const char *path, struct stat *buf, int flags) {
    static int (*real)(int, int, const char *, struct stat *, int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "__fxstatat");
    return real(ver, dirfd, redirect(path), buf, flags);
}
SHIM
}

# Main
main "$@"
