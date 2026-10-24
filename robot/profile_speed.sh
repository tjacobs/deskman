#!/usr/bin/env bash
# Play a spoken question into the robot microphone and print the reply timing.

# Exit on error
set -euo pipefail

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_DIR="${SCRIPT_DIR}/../talk"
PYTHON="${TALK_DIR}/.venv/bin/python"
TALK_SCRIPT="${TALK_DIR}/talk.py"
LOG_FILE="${SCRIPT_DIR}/log.txt"
QUESTION_WAV="/tmp/profile_question.wav"
PROMPT_FILE="/tmp/profile_question.txt"

# Defaults
DEFAULT_PROMPT="Deskman, what is the time?"
VOLUME_PERCENT=100
START_WAIT_SECONDS=60
PLAY_TRIES=15
REPLY_WAIT_SECONDS=25

# Prompt to ask
PROMPT="${DEFAULT_PROMPT}"

# Main
main() {
    # Read command line arguments
    parse_args "$@"

    # Explain the timing fields
    print_legend

    # Ensure talk.py is running with timing enabled
    ensure_talk_timing

    # Set speaker volume to full so the microphone hears the question
    set_volume

    # Generate the spoken question wav when missing or changed
    ensure_question_wav

    # Play the question and print the timing from the robot log
    play_and_measure
}

# Parse command line arguments
parse_args() {
    # Show help when requested
    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        echo "Usage: ./profile_speed.sh [question...]"
        exit 0
    fi

    # Use custom question when passed
    if [[ "$#" -gt 0 ]]; then
        PROMPT="$*"
    fi
}

# Print a short explanation of the timing fields
print_legend() {
    # Describe each step in the Timing line
    echo "Timing fields:"
    echo "  silero  — pause after your last word until Silero stops recording"
    echo "  whisper — GPU speech-to-text transcription"
    echo "  beep    — wake tone played after hearing the wake word"
    echo "  gemma   — LLM reply generation, including any tool calls"
    echo "  kokoro  — GPU text-to-speech for the first audio chunk"
    echo "  total   — full time from last spoken word to first reply audio"
    echo
}

# Ensure talk.py is running with --timing or TALK_TIMING set
ensure_talk_timing() {
    # Check whether an existing talk.py already has timing enabled
    local pid
    pid=$(pgrep -f "talk/talk.py" | head -n 1 || true)
    if [[ -n "${pid}" ]] && talk_has_timing "${pid}"; then
        return
    fi

    # Warn and stop the old talk.py when it was started without timing
    if [[ -n "${pid}" ]]; then
        echo "Warning: talk.py pid ${pid} is running without --timing, restarting with --timing..."
        kill -TERM "${pid}" 2>/dev/null || true
        while kill -0 "${pid}" 2>/dev/null; do
            sleep 0.2
        done
    else
        echo "Warning: talk.py is not running, starting with --timing..."
    fi

    # Start talk.py with --timing appending to the robot log before robot.service respawns it
    local start_line
    start_line=$(wc -l < "${LOG_FILE}")
    start_line=$((start_line + 1))
    PYTHONUNBUFFERED=1 TALK_TIMING=1 nohup "${PYTHON}" "${TALK_SCRIPT}" --timing >> "${LOG_FILE}" 2>&1 &

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

# Return zero when the given talk.py pid has --timing in cmdline or TALK_TIMING in environ
talk_has_timing() {
    # Check command line arguments and environment for the process
    local pid="$1"
    if tr '\0' '\n' < "/proc/${pid}/cmdline" 2>/dev/null | grep -qx -- "--timing"; then
        return 0
    fi
    if tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null | grep -q "^TALK_TIMING=.\+"; then
        return 0
    fi
    return 1
}

# Set the speaker volume to full
set_volume() {
    # Run the talk volume helper
    "${PYTHON}" -c "import sys; sys.path.insert(0, '${TALK_DIR}/text'); import volume; volume.run_set_volume({'percent': ${VOLUME_PERCENT}})"
    echo "Set volume to ${VOLUME_PERCENT} percent."
}

# Generate the question wav with kokoro on CPU when not already cached
ensure_question_wav() {
    # Reuse the cached wav when the prompt matches
    if [[ -f "${QUESTION_WAV}" && -f "${PROMPT_FILE}" ]] && [[ "$(cat "${PROMPT_FILE}")" == "${PROMPT}" ]]; then
        return
    fi

    # Synthesize the prompt with kokoro on CPU so GPU memory stays free
    echo "Generating question wav: ${PROMPT}"
    CUDA_VISIBLE_DEVICES="" "${PYTHON}" - "${TALK_DIR}" "${PROMPT}" "${QUESTION_WAV}" <<'PY'
# Imports
import sys
import numpy
import soundfile

# Read arguments and import talk helpers
talk_dir, prompt, wav_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, talk_dir)
import utils

# Use cached voice and silence torch warnings before importing kokoro
utils.enable_offline_if_cached(utils.DEFAULT_VOICE)
utils.suppress_torch_warnings()
import kokoro

# Synthesize the prompt and write the wav file
pipeline = kokoro.KPipeline(lang_code=utils.DEFAULT_VOICE[0], repo_id=utils.REPO_ID)
chunks = [chunk.audio.numpy() for chunk in pipeline(prompt, voice=utils.DEFAULT_VOICE, speed=1.0)]
soundfile.write(wav_path, numpy.concatenate(chunks), 24000)
PY

    # Save the prompt that produced this wav
    printf '%s' "${PROMPT}" > "${PROMPT_FILE}"
}

# Play the question wav and wait for the Timing line in the robot log
play_and_measure() {
    # Record the log position before playing
    local start_line
    start_line=$(wc -l < "${LOG_FILE}")
    start_line=$((start_line + 1))

    # Resolve the shared ALSA playback command from talk/utils.py
    local device
    device=$("${PYTHON}" -c "import sys; sys.path.insert(0, '${TALK_DIR}'); import utils; print(utils.shared_playback_device(utils.find_usb_card()))")

    # Play the wav, retrying briefly if the speaker is busy
    echo "Asking: ${PROMPT}"
    local try
    for try in $(seq 1 "${PLAY_TRIES}"); do
        if aplay -q -D "${device}" "${QUESTION_WAV}" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    # Wait for the total timing line or the end of the turn after Reply in the robot log
    local second
    for second in $(seq 1 "${REPLY_WAIT_SECONDS}"); do
        if tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^  total "; then
            break
        fi
        if tail -n +"${start_line}" "${LOG_FILE}" | sed -n '/^Reply:/,$p' | grep -a -qE "^(Listening\.\.\.|Idle\.)$"; then
            break
        fi
        sleep 1
    done

    # Print the turn lines from the log
    tail -n +"${start_line}" "${LOG_FILE}" | grep -a -E "Heard:|Command:|\[Tool\]|Reply:|Timing:|^  (silero|whisper|beep|gemma|kokoro|total) " || true

    # Warn when no timing block was logged for the reply
    if ! tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^  total "; then
        echo "Warning: no Timing block found in ${LOG_FILE}, make sure talk.py was started with --timing or TALK_TIMING=1."
    fi
}

# Main
main "$@"
