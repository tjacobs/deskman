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

# Answer follow-ups about the last volume that was actually set
def answer_last_volume_set(prompt):
    if last_volume_percent is None:
        return None
    if "voice" in prompt.lower():
        return None
    if not re.search(r"\bwhat did you set\b", prompt.lower()):
        return None
    return f"I set the volume to {last_volume_percent} percent."

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
    if re.search(r"\bwhat did you set\b", text) and "voice" not in text:
        return True
    if "volume" not in text:
        return False
    if needs_set_volume(prompt):
        return False
    return bool(re.search(r"\b(what|how|current|check|get|tell)\b", text))

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
    if last_volume_percent is not None:
        return f"I have set the volume to {last_volume_percent} percent."
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
    if not set_speaker_volume(percent):
        return "Could not set the volume."
    actual = read_speaker_volume()
    if actual is None:
        last_volume_percent = percent
        return f"Volume set to {percent} percent."
    last_volume_percent = actual
    return f"Volume set to {actual} percent."

# Read speaker volume for the tool
def run_get_volume(arguments=None):
    global last_volume_percent
    percent = read_speaker_volume()
    if percent is None:
        return "Could not read the volume."
    last_volume_percent = percent
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

# Set the USB speaker volume with amixer
def set_speaker_volume(percent):
    card = find_volume_card()
    control = find_volume_control(card)
    if card is None or control is None:
        return False
    result = subprocess.run(["amixer", "-c", str(card), "set", control, f"{percent}%", "unmute"], capture_output=True, text=True)
    return result.returncode == 0

# Read the current USB speaker volume percent
def read_speaker_volume():
    card = find_volume_card()
    control = find_volume_control(card)
    if card is None or control is None:
        return None
    result = subprocess.run(["amixer", "-c", str(card), "get", control], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    match = re.search(r"\[(\d+)%\]", result.stdout)
    if not match:
        return None
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

    # Prefer the speaker-only card, one without a mic
    for card_index in usb_cards:
        if not volume_card_has_capture(card_index):
            return card_index
    if usb_cards:
        return usb_cards[0]
    return 0 if os.path.exists("/proc/asound/card0") else None

# Return true when a card has a capture stream
def volume_card_has_capture(card_index):
    stream_path = f"/proc/asound/card{card_index}/stream0"
    if not os.path.isfile(stream_path):
        return False
    with open(stream_path) as stream_file:
        return "Capture:" in stream_file.read()

# Main
if __name__ == "__main__":
    main()
