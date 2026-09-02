#!/usr/bin/env python3

# Speaker volume control through amixer

# Imports
import os
import re
import subprocess

# Config
VOLUME_CONTROLS = ("Speaker", "PCM", "Master")
VOLUME_RETRY_PROMPT = "Do not guess. Call set_volume now with the requested percent, then answer using only the tool result."
GET_VOLUME_RETRY_PROMPT = "Do not guess. Call get_volume now, then answer using only the tool result."

# Tools the local model can call for volume
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the speaker volume to a percent from 0 to 100.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "number",
                        "description": "Volume percent from 0 to 100.",
                    },
                },
                "required": ["percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Get the current speaker volume percent.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# State
last_volume_percent = None

# Main
def main():
    # Print the current volume when run with no args
    print(run_get_volume())

# Spoken confirmation after a successful volume set
def confirm_volume_set():
    if last_volume_percent is None:
        return None
    return f"I have set the volume to {last_volume_percent} percent."

# Return true when the question needs a volume tool
def needs_volume_tool(prompt):
    return needs_set_volume(prompt) or needs_get_volume(prompt)

# Return true when the user wants the volume changed
def needs_set_volume(prompt):
    text = prompt.lower()

    # Sonos volume uses set_sonos_volume, not the desk speaker
    if is_sonos_volume_request(prompt):
        return False
    if re.search(r"\b(louder|quieter|mute|unmute)\b", text):
        return True
    if "volume" not in text:
        return False
    if re.search(r"\d+\s*%", text):
        return True
    return bool(re.search(r"\b(set|change|make|turn|raise|lower)\b", text))

# Return true when the user only wants the current volume
def needs_get_volume(prompt):
    text = prompt.lower()

    # Sonos volume uses Sonos tools, not the desk speaker
    if is_sonos_volume_request(prompt):
        return False
    if "volume" not in text:
        return False
    if needs_set_volume(prompt):
        return False
    return bool(re.search(r"\b(what|how|current|check|get|tell)\b", text))

# Return true when volume is aimed at Sonos, music, song, or a Sonos room
def is_sonos_volume_request(prompt):
    import accounts.sonos as sonos_account
    return sonos_account.needs_set_sonos_volume(prompt)

# Retry set_volume once, then apply it in Python if still missing
def force_set_volume(prompt, messages, message, already_retried, record_tool):
    percent = parse_volume_percent(prompt)

    # First miss, ask the model again with an explicit set_volume order
    if not already_retried:
        print("[volume] missing set_volume, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if percent is None:
            retry = VOLUME_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call set_volume with percent {percent} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, set it directly in Python
    if percent is None:
        return None
    arguments = {"percent": percent}
    result = run_set_volume(arguments)
    record_tool("set_volume", arguments, result)
    print(f"[volume] forced set_volume -> {result}", flush=True)
    return f"I have set the volume to {percent} percent."

# Set speaker volume from tool arguments
def run_set_volume(arguments):
    global last_volume_percent
    if "percent" not in arguments:
        return "Volume percent is required."
    try:
        percent = int(round(float(arguments["percent"])))
    except (TypeError, ValueError):
        return "Volume percent must be a number."
    percent = max(0, min(100, percent))
    error = set_speaker_volume(percent)
    if error:
        return error

    # Remember the requested value, ALSA rounds and should not be spoken back
    last_volume_percent = percent
    return f"Volume set to {percent} percent."

# Read speaker volume for the tool
def run_get_volume(arguments=None):
    percent = read_speaker_volume()
    if isinstance(percent, str):
        return percent
    return f"Volume is {percent} percent."

# Read a volume percent from the user text when present
def parse_volume_percent(prompt):
    text = prompt.lower()
    if re.search(r"\bmute\b", text) and not re.search(r"\bunmute\b", text):
        return 0
    match = re.search(r"(\d+)\s*%", text)
    if match:
        return max(0, min(100, int(match.group(1))))
    match = re.search(r"\b(?:to|at)\s+(\d+)\b", text)
    if match:
        return max(0, min(100, int(match.group(1))))
    return None

# Set the USB speaker volume with amixer, return an error string or None
def set_speaker_volume(percent):
    card = find_volume_card()
    if card is None:
        return "No USB speaker card found."
    control = find_volume_control(card)
    if control is None:
        return f"No speaker volume control on card {card}."
    result = subprocess.run(["amixer", "-c", str(card), "set", control, f"{percent}%", "unmute"], capture_output=True, text=True)
    if result.returncode == 0:
        return None
    return command_error(result, f"amixer set failed on card {card} {control}.")

# Read the current USB speaker volume percent, or an error string
def read_speaker_volume():
    card = find_volume_card()
    if card is None:
        return "No USB speaker card found."
    control = find_volume_control(card)
    if control is None:
        return f"No speaker volume control on card {card}."
    result = subprocess.run(["amixer", "-c", str(card), "get", control], capture_output=True, text=True)
    if result.returncode != 0:
        return command_error(result, f"amixer get failed on card {card} {control}.")
    match = re.search(r"\[(\d+)%\]", result.stdout)
    if not match:
        return f"No volume percent in amixer output on card {card} {control}."
    return int(match.group(1))

# Find the first playback volume control on a card
def find_volume_control(card):
    if card is None:
        return None
    for control in VOLUME_CONTROLS:
        result = subprocess.run(["amixer", "-c", str(card), "get", control], capture_output=True, text=True)
        if result.returncode == 0 and re.search(r"\[\d+%\]", result.stdout):
            return control
    return None

# Find the USB playback card index used for speech
def find_volume_card():
    cards_path = "/proc/asound/cards"
    if not os.path.isfile(cards_path):
        return 0 if os.path.exists("/proc/asound/card0") else None

    # Collect USB card indexes
    usb_cards = []
    with open(cards_path) as cards_file:
        for line in cards_file:
            if "USB-Audio" not in line:
                continue
            card_index_text = line.strip().split(None, 1)[0]
            if card_index_text.isdigit():
                usb_cards.append(int(card_index_text))

    # Skip cards with no speaker control, camera dummies only have Mic
    speaker_cards = []
    for card_index in usb_cards:
        if find_volume_control(card_index) is None:
            continue
        speaker_cards.append(card_index)

    # Prefer the speaker-only card, one without a mic
    for card_index in speaker_cards:
        if not volume_card_has_capture(card_index):
            return card_index
    if speaker_cards:
        return speaker_cards[0]
    return None

# Return true when a card has a capture stream
def volume_card_has_capture(card_index):
    stream_path = f"/proc/asound/card{card_index}/stream0"
    if not os.path.isfile(stream_path):
        return False
    with open(stream_path) as stream_file:
        return "Capture:" in stream_file.read()

# Prefer stderr from amixer, then stdout, then a fallback
def command_error(result, fallback):
    text = (result.stderr or "").strip()
    if not text:
        text = (result.stdout or "").strip()
    if text:
        return text
    return fallback

# Main
if __name__ == "__main__":
    main()
