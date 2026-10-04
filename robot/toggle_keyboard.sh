#!/usr/bin/env bash
# Show or hide the touch keyboard, run from the taskbar button.

set -euo pipefail

# Layout installed next to the stock matchbox ones, digits and web punctuation over the letters
LAYOUT=deskman

# Main
main() {
    # Tapping the button again takes the keyboard away
    keyboard_pids="$(pidof matchbox-keyboard || true)"
    if [[ -n "${keyboard_pids}" ]]; then
        kill ${keyboard_pids}
        return
    fi

    # Start it detached, the openbox rule docks it along the bottom above everything else
    setsid matchbox-keyboard "${LAYOUT}" >/dev/null 2>&1 </dev/null &
}

# Main
main "$@"
