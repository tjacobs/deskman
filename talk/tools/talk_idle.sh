#!/usr/bin/env bash
# Shared helpers to bring talk.py back to idle, sourced by order_drink.sh and profile_speed.sh.
# The sourcing script sets PYTHON, TALK_DIR, LOG_FILE, QUESTION_VOICE, and PLAY_TRIES.

# Config the line that closes the conversation, and the wav it is rendered to
QUIET_LINE="Quiet."
QUIET_WAV="/tmp/words_said_quiet.wav"

# Config how often to say it, and how long to wait for idle each time
IDLE_TRIES=3
IDLE_WAIT_SECONDS=10

# Print idle or listening from the last status line talk.py logged
talk_state() {
    # Read the newest status line, talk.py prints Idle when ready and Listening when a follow-up is open
    local last
    last=$(grep -a -E "^(Idle\.|Listening\.\.\.)$" "${LOG_FILE}" | tail -n 1 || true)
    if [[ "${last}" == "Idle." ]]; then
        echo "idle"
    else
        echo "listening"
    fi
}

# Say the quiet line until talk.py is idle, so the next question needs the wake word and nothing is left open
go_idle() {
    # Skip when talk.py is already idle
    if [[ "$(talk_state)" == "idle" ]]; then
        echo "Robot is idle."
        return
    fi

    # Render the quiet line through the running talk.py when it is missing
    if [[ ! -f "${QUIET_WAV}" ]]; then
        "${PYTHON}" "${TALK_DIR}/tools/render_line.py" "${QUESTION_VOICE}" "${QUIET_WAV}" "${QUIET_LINE}"
    fi

    # Say it, then wait for talk.py to log idle
    local try
    local second
    local start_line
    for try in $(seq 1 "${IDLE_TRIES}"); do
        start_line=$(( $(wc -l < "${LOG_FILE}") + 1 ))
        echo "Saying: ${QUIET_LINE}"
        play_quiet_wav
        for second in $(seq 1 "${IDLE_WAIT_SECONDS}"); do
            if tail -n +"${start_line}" "${LOG_FILE}" | grep -a -q "^Idle\.$"; then
                echo "Robot is idle."
                return
            fi
            sleep 1
        done
    done

    # Warn when it never went idle
    echo "Warning: robot did not go idle after ${IDLE_TRIES} tries."
}

# Play the quiet wav on the shared speaker, retrying briefly when it is busy
play_quiet_wav() {
    # Resolve the shared ALSA playback command from talk/utils.py
    local device
    device=$("${PYTHON}" -c "import sys; sys.path.insert(0, '${TALK_DIR}'); import utils; print(utils.shared_playback_device(utils.find_usb_card()))")

    # Play the wav
    local try
    for try in $(seq 1 "${PLAY_TRIES}"); do
        if aplay -q -D "${device}" "${QUIET_WAV}" 2>/dev/null; then
            return
        fi
        sleep 1
    done
}
