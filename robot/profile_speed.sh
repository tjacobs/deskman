#!/usr/bin/env bash
# Play a spoken question into the robot microphone and print the reply timing.

# Exit on error
set -euo pipefail

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALK_DIR="$(cd "${SCRIPT_DIR}/../talk" && pwd)"
PYTHON="${TALK_DIR}/.venv/bin/python"
LOG_FILE="${SCRIPT_DIR}/log.txt"
QUESTION_WAV="/tmp/profile_question.wav"
PROMPT_FILE="/tmp/profile_question.txt"

# Defaults
DEFAULT_PROMPT="Deskman. What is the time?"
VOLUME_PERCENT=80
START_WAIT_SECONDS=60
PLAY_TRIES=15
HEAR_WAIT_SECONDS=10
REPLY_WAIT_SECONDS=25

# Prompt to ask
PROMPT="${DEFAULT_PROMPT}"

# Main
main() {
    # Read command line arguments
    parse_args "$@"

    # Ensure talk.py is running with timing enabled
    ensure_talk_timing

    # Say which runtime is running Gemma
    print_gemma_runtime

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

# Ensure talk.py is running with timing on, restarting the services when it is missing or stale
ensure_talk_timing() {
    # Keep the running talk.py when it started after the last edit to the talk code
    local pid
    pid=$(pgrep -f "talk/talk.py" | head -n 1 || true)
    if [[ -n "${pid}" ]] && talk_is_current "${pid}"; then
        # Warn when timing is off, it comes from timing in talk/config.json
        if ! talk_has_timing "${pid}"; then
            echo "Warning: timing is off, set timing true in talk/config.json and restart the services."
        fi
        return
    fi

    # Say why, then restart the services so the robot spawns a fresh talk.py
    if [[ -n "${pid}" ]]; then
        echo "Restarting services, talk.py pid ${pid} is older than the talk code..."
    else
        echo "Restarting services, talk.py is not running..."
    fi
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

# Print whether llama.cpp or LiteRT is running Gemma
print_gemma_runtime() {
    # Look for each text server process
    local litert_running=false
    local llama_running=false
    if pgrep -f "litert_server.py" > /dev/null; then
        litert_running=true
    fi
    if pgrep -f "llama-server" > /dev/null; then
        llama_running=true
    fi

    # Print the runtime, or a warning when it is unclear
    if [[ "${litert_running}" == true && "${llama_running}" == false ]]; then
        echo "Gemma runtime: LiteRT"
    elif [[ "${llama_running}" == true && "${litert_running}" == false ]]; then
        echo "Gemma runtime: llama.cpp"
    elif [[ "${litert_running}" == true ]]; then
        echo "Warning: both LiteRT and llama.cpp servers are running, port 8080 may be shared."
    else
        echo "Warning: no local Gemma server found, talk.py may be using the cloud."
    fi
}

# Return zero when the given talk.py pid has --timing in cmdline, TALK_TIMING in environ, or timing true in config.json
talk_has_timing() {
    # Check command line arguments and environment for the process
    local pid="$1"
    if tr '\0' '\n' < "/proc/${pid}/cmdline" 2>/dev/null | grep -qx -- "--timing"; then
        return 0
    fi
    if tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null | grep -q "^TALK_TIMING=.\+"; then
        return 0
    fi
    if grep -q '"timing": *true' "${TALK_DIR}/config.json" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Return zero when the given talk.py pid started after any talk Python file was last modified
talk_is_current() {
    # Find the newest edit time across the talk Python files
    local pid="$1"
    local process_age
    local newest_edit
    local file_age
    newest_edit=$(find "${TALK_DIR}" -name '*.py' -not -path '*/.venv/*' -not -path '*/llama.cpp/*' -not -name 'litert_server.py' -printf '%T@\n' | sort -n | tail -n 1)

    # Compare process age in seconds against file age in seconds
    process_age=$(ps -o etimes= -p "${pid}" 2>/dev/null | tr -d ' ' || echo 0)
    file_age=$(( $(date +%s) - ${newest_edit%.*} ))
    [[ "${process_age}" -le "${file_age}" ]]
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

    # Wait up to 10 seconds for the robot to hear the question
    local second
    for second in $(seq 1 "${HEAR_WAIT_SECONDS}"); do
        if tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^Command:"; then
            break
        fi
        sleep 1
    done

    # Stop when the robot did not hear the question within the timeout
    if ! tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^Command:"; then
        tail -n +"${start_line}" "${LOG_FILE}" | grep -a "^Heard:" || true
        echo "Warning: robot did not hear the question after ${HEAR_WAIT_SECONDS} seconds."
        return
    fi

    # Wait for the total timing line or the end of the turn after Reply in the robot log
    for second in $(seq 1 "${REPLY_WAIT_SECONDS}"); do
        if tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^  Total "; then
            break
        fi
        if tail -n +"${start_line}" "${LOG_FILE}" | sed -n '/^Reply:/,$p' | grep -a -qE "^(Listening\.\.\.|Idle\.)$"; then
            break
        fi
        sleep 1
    done

    # Print the turn lines from the log with descriptions on the timing lines
    tail -n +"${start_line}" "${LOG_FILE}" | grep -a -E "Heard:|Command:|\[Tool\]|Reply:|Timing:|^  (Silero|Whisper|Gemma|Kokoro|Total) " | sed \
        -e 's/^\(  Silero .*sec\)$/\1  — Time from last word of question/' \
        -e 's/^\(  Whisper .*sec\)$/\1  — Speech to text transcription time/' \
        -e 's/^\(  Gemma .*sec\)$/\1  — LLM reply generation, including any tool calls/' \
        -e 's/^\(  Kokoro .*sec\)$/\1  — Text to speech time for the first chunk of audio/' \
        -e 's/^\(  Total .*sec\)$/\1  — Full time from last spoken word to first reply audio/' || true

    # Warn when no timing block was logged for the reply
    if ! tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^  Total "; then
        echo "Warning: no Timing block found in ${LOG_FILE}, make sure talk.py was started with --timing or TALK_TIMING=1."
    fi
}

# Main
main "$@"
