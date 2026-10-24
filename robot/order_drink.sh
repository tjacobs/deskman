#!/usr/bin/env bash
# Play spoken drink ordering lines into the robot microphone and print each reply.

# Exit on error
set -euo pipefail

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_DIR="$(cd "${SCRIPT_DIR}/../talk" && pwd)"
PYTHON="${TALK_DIR}/.venv/bin/python"
LOG_FILE="${SCRIPT_DIR}/log.txt"
WAV_PREFIX="/tmp/order_drink_"

# Defaults
QUESTION_VOICE="am_adam"
WAKE_PHRASE="Deskman."
DEFAULT_LINES=("Start drink ordering app." "I would like lime soda please." "Do you have any coke?" "I will have a blackberry please." "Stop drink ordering.")
VOLUME_PERCENT=80
START_WAIT_SECONDS=60
PLAY_TRIES=15
HEAR_WAIT_SECONDS=10
REPLY_WAIT_SECONDS=30
TURN_GAP_SECONDS=1

# Lines to say
SPOKEN_LINES=()

# Main
main() {
    # Read command line arguments
    parse_args "$@"

    # Generate a spoken wav for each line
    ensure_line_wavs

    # Ensure talk.py is running and has the drink ordering code
    ensure_talk_current

    # Set speaker volume so the microphone hears the lines
    set_volume

    # Say each line and print what the robot heard and replied
    local line
    for line in "${SPOKEN_LINES[@]}"; do
        play_and_report "${line}"
        sleep "${TURN_GAP_SECONDS}"
    done
}

# Parse command line arguments
parse_args() {
    # Show help when requested
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        echo "Usage: ./order_drink.sh [line...]"
        echo "  Each argument is one spoken line, the wake word is added to the first one."
        echo "  (no arg)  start drink ordering, order, ask for coke, order again, then stop"
        exit 0
    fi

    # Use the custom lines when passed, otherwise the default order
    if [[ "$#" -gt 0 ]]; then
        SPOKEN_LINES=("$@")
    else
        SPOKEN_LINES=("${DEFAULT_LINES[@]}")
    fi

    # Say the wake word on the first line only, the conversation stays open for the rest
    SPOKEN_LINES[0]="${WAKE_PHRASE} ${SPOKEN_LINES[0]}"
}

# Name the wav file for one spoken line
line_wav_path() {
    # Hash the full spoken text so a changed line gets a new file
    local hash
    hash=$(printf '%s' "${QUESTION_VOICE} $1" | md5sum | cut -c1-12)
    echo "${WAV_PREFIX}${hash}.wav"
}

# Generate the wav for each line that is not already cached
ensure_line_wavs() {
    # Build the list of wav path and text pairs that are missing
    local pairs=()
    local line
    local path
    for line in "${SPOKEN_LINES[@]}"; do
        path=$(line_wav_path "${line}")
        if [[ ! -f "${path}" ]]; then
            pairs+=("${path}" "${line}")
        fi
    done

    # Skip loading kokoro when every wav is cached
    if [[ "${#pairs[@]}" -eq 0 ]]; then
        return
    fi

    # Synthesize the missing lines with kokoro on CPU so GPU memory stays free
    echo "Generating $(( ${#pairs[@]} / 2 )) spoken lines..."
    CUDA_VISIBLE_DEVICES="" "${PYTHON}" - "${TALK_DIR}" "${QUESTION_VOICE}" "${pairs[@]}" <<'PY'
# Imports
import sys
import numpy
import soundfile

# Read arguments and import talk helpers
talk_dir = sys.argv[1]
voice = sys.argv[2]
pairs = sys.argv[3:]
sys.path.insert(0, talk_dir)
import utils

# Use cached voice and silence torch warnings before importing kokoro
utils.enable_offline_if_cached(voice)
utils.suppress_torch_warnings()
import kokoro

# Synthesize each line and write its wav file
pipeline = kokoro.KPipeline(lang_code=voice[0], repo_id=utils.REPO_ID)
for index in range(0, len(pairs), 2):
    chunks = [chunk.audio.numpy() for chunk in pipeline(pairs[index + 1], voice=voice, speed=1.0)]
    soundfile.write(pairs[index], numpy.concatenate(chunks), 24000)
PY
}

# Ensure talk.py is running and started after the last edit to a talk Python file
ensure_talk_current() {
    # Keep the running talk.py when it is up to date
    local pid
    pid=$(pgrep -f "talk/talk.py" | head -n 1 || true)
    if [[ -n "${pid}" ]] && talk_is_current "${pid}"; then
        wait_for_talk_ready
        return
    fi

    # Restart the services, the robot spawns a fresh talk.py
    echo "Restarting services to load the latest talk code..."
    # Stop the old talk.py first, the robot leaves one it did not start running
    if [[ -n "${pid}" ]]; then
        kill -TERM "${pid}" 2>/dev/null || true
        while kill -0 "${pid}" 2>/dev/null; do
            sleep 0.2
        done
    fi

    # Restart the services, then read the log from this point
    local start_line
    start_line=$(( $(wc -l < "${LOG_FILE}") + 1 ))
    "${SCRIPT_DIR}/restart_services.sh"

    # Wait for talk.py to finish loading models
    echo "Waiting for talk.py to finish starting..."
    local second
    for second in $(seq 1 "${START_WAIT_SECONDS}"); do
        if tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "Started in "; then
            return
        fi
        sleep 1
    done
    echo "Warning: timed out waiting for talk.py startup line in ${LOG_FILE}"
}

# Wait until the newest talk.py has printed its startup line, it prints Started in after loading
wait_for_talk_ready() {
    local second
    local ready
    for second in $(seq 1 "${START_WAIT_SECONDS}"); do
        ready=$(awk '/^Loading whisper/ {loading=NR} /^Started in / {started=NR} END {print (started > loading) ? "yes" : "no"}' "${LOG_FILE}")
        if [[ "${ready}" == "yes" ]]; then
            return
        fi
        sleep 1
    done
    echo "Warning: timed out waiting for talk.py to finish starting."
}

# Return zero when the given talk.py pid started after any talk Python file was last modified
talk_is_current() {
    # Find the newest edit time across the talk Python files
    local pid="$1"
    local newest_edit
    newest_edit=$(find "${TALK_DIR}" -name '*.py' -not -path '*/.venv/*' -not -path '*/llama.cpp/*' -not -path '*/litert/*' -not -name 'litert_server.py' -printf '%T@\n' | sort -n | tail -n 1)

    # Compare process age in seconds against file age in seconds
    local process_age
    local file_age
    process_age=$(ps -o etimes= -p "${pid}" 2>/dev/null | tr -d ' ' || echo 0)
    file_age=$(( $(date +%s) - ${newest_edit%.*} ))
    [[ "${process_age}" -le "${file_age}" ]]
}

# Set the speaker volume
set_volume() {
    # Run the talk volume helper
    "${PYTHON}" -c "import sys; sys.path.insert(0, '${TALK_DIR}/text'); import volume; volume.run_set_volume({'percent': ${VOLUME_PERCENT}})"
    echo "Set volume to ${VOLUME_PERCENT} percent."
}

# Play one line, wait for the robot to finish replying, and print the turn from the log
play_and_report() {
    # Record the log position before playing
    local line="$1"
    local start_line
    start_line=$(( $(wc -l < "${LOG_FILE}") + 1 ))

    # Resolve the shared ALSA playback command from talk/utils.py
    local device
    device=$("${PYTHON}" -c "import sys; sys.path.insert(0, '${TALK_DIR}'); import utils; print(utils.shared_playback_device(utils.find_usb_card()))")

    # Play the wav, retrying briefly if the speaker is busy
    echo ""
    echo "Saying: ${line}"
    local try
    for try in $(seq 1 "${PLAY_TRIES}"); do
        if aplay -q -D "${device}" "$(line_wav_path "${line}")" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    # Wait for the robot to hear the line
    local second
    for second in $(seq 1 "${HEAR_WAIT_SECONDS}"); do
        if tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^Command:"; then
            break
        fi
        sleep 1
    done

    # Stop when the robot did not hear the line within the timeout
    if ! tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^Command:"; then
        tail -n +"${start_line}" "${LOG_FILE}" | grep -a "^Heard:" || true
        echo "Warning: robot did not hear the line after ${HEAR_WAIT_SECONDS} seconds."
        return
    fi

    # Wait until the reply has been spoken, the log goes back to listening or idle
    for second in $(seq 1 "${REPLY_WAIT_SECONDS}"); do
        if tail -n +"${start_line}" "${LOG_FILE}" | sed -n '/^Reply:/,$p' | grep -a -qE "^(Listening\.\.\.|Idle\.)$"; then
            break
        fi
        sleep 1
    done

    # Print what was heard, any tools, and the reply
    tail -n +"${start_line}" "${LOG_FILE}" | grep -a -E "^(Heard|Command|Reply):|\[Tool\]" || true
}

# Main
main "$@"
