#!/usr/bin/env bash
# Sample memory and talk-related processes into a log

# Stop on errors
set -euo pipefail

# Paths and thresholds
SPEAK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${SPEAK_DIR}/monitor.log"
WARN_AVAILABLE_MB=400
CRITICAL_AVAILABLE_MB=200

# Main
main() {
    # Create log file
    mkdir -p "$(dirname "${LOG_FILE}")"
    touch "${LOG_FILE}"

    # Read memory stats
    local available_mb total_mb used_mb swap_used_mb
    available_mb="$(awk '/MemAvailable:/ {printf "%.0f", $2/1024}' /proc/meminfo)"
    total_mb="$(awk '/MemTotal:/ {printf "%.0f", $2/1024}' /proc/meminfo)"
    used_mb="$(awk '/MemTotal:/ {total=$2} /MemAvailable:/ {printf "%.0f", (total-$2)/1024}' /proc/meminfo)"
    swap_used_mb="$(awk '/SwapTotal:/ {total=$2} /SwapFree:/ {printf "%.0f", (total-$2)/1024}' /proc/meminfo)"

    # Read load and uptime
    local load uptime_text
    load="$(cut -d' ' -f1-3 /proc/loadavg)"
    uptime_text="$(uptime -p 2>/dev/null || true)"

    # Read talk-related process RSS in MB
    local talk_rss llama_rss real_rss
    talk_rss="$(process_rss_mb 'talk.py')"
    llama_rss="$(process_rss_mb 'llama-server')"
    real_rss="$(process_rss_mb 'real.py')"

    # Append one summary line
    local stamp level
    stamp="$(date -Is)"
    level="ok"
    if (( available_mb < CRITICAL_AVAILABLE_MB )); then
        level="critical"
    elif (( available_mb < WARN_AVAILABLE_MB )); then
        level="warn"
    fi
    echo "${stamp} ${level} avail=${available_mb}MB used=${used_mb}/${total_mb}MB swap=${swap_used_mb}MB load=${load} talk=${talk_rss}MB llama=${llama_rss}MB real=${real_rss}MB ${uptime_text}" >> "${LOG_FILE}"

    # On low memory, dump more detail and flush to disk
    if (( available_mb < WARN_AVAILABLE_MB )); then
        dump_detail "${stamp}" "${available_mb}"
        sync
    fi

    # Keep the log from growing forever
    trim_log
}

# Return RSS MB for the first matching process, or 0
process_rss_mb() {
    local pattern="$1"
    local rss_kb
    rss_kb="$(ps -eo rss,args --no-headers | awk -v pattern="${pattern}" 'index($0, pattern) {print $1; exit}')"
    if [[ -z "${rss_kb}" ]]; then
        echo 0
        return
    fi
    awk -v rss_kb="${rss_kb}" 'BEGIN {printf "%.0f", rss_kb/1024}'
}

# Append top processes and recent OOM lines when memory is low
dump_detail() {
    local stamp="$1"
    local available_mb="$2"
    {
        echo "--- detail ${stamp} avail=${available_mb}MB ---"
        free -h
        echo "Top memory:"
        ps -eo pid,rss,pmem,comm,args --sort=-rss | head -15
        echo "OOM / kill hints:"
        dmesg -T 2>/dev/null | rg -i 'out of memory|oom-killer|killed process|Memory cgroup' | tail -20 || true
        journalctl -k --no-pager -n 80 2>/dev/null | rg -i 'out of memory|oom-killer|killed process' | tail -20 || true
        echo "--- end detail ---"
    } >> "${LOG_FILE}"
}

# Keep only the last 2000 lines
trim_log() {
    local lines
    lines="$(wc -l < "${LOG_FILE}")"
    if (( lines > 2000 )); then
        tail -n 1500 "${LOG_FILE}" > "${LOG_FILE}.tmp"
        mv "${LOG_FILE}.tmp" "${LOG_FILE}"
    fi
}

# Run
main
