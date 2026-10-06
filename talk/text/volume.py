#!/usr/bin/env python3

# Speaker volume through amixer, plus a fixed microphone level and auto gain

# Imports
import argparse
import math
import os
import re
import subprocess

# Config
VOLUME_CONTROLS = ("Speaker", "PCM", "Master")
AGC_CONTROL = "Auto Gain Control"
MIC_CAPTURE_PERCENT = 100
MIC_PLAYBACK_VOLUME = "Mic Playback Volume"
MIC_CAPTURE_VOLUME = "Mic Capture Volume"
MIC_PLAYBACK_SWITCH = "Mic Playback Switch"
MIC_CAPTURE_SWITCH = "Mic Capture Switch"

# Alsamixer switches to a log curve once the dB span is wider than this
MIC_LINEAR_DB_SPAN = 24.0
VOLUME_RETRY_PROMPT = "Do not guess. Call set_volume now with the requested percent, then answer using only the tool result."
GET_VOLUME_RETRY_PROMPT = "Do not guess. Call get_volume now, then answer using only the tool result."
VOLUME_SET_REPLY = "Set to {percent} percent."

# Config spoken check, cached as a wav per phrase so later taps play at once
TALK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAY_WAV_PREFIX = "say_"
SAY_SAMPLE_RATE = 24000
SAY_SPEED = 1.2

# Tools the local model can call for volume
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the speaker volume to a percent from 0 to 100. Call this immediately. Do not speak first. Do not call get_volume first.",
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
    args = parse_args()

    # Print the current volume when run with no args, otherwise set it
    if args.percent is None:
        print(run_get_volume())
    else:
        print(run_set_volume({"percent": args.percent}))

    # Speak a phrase at the new level so it can be heard
    if args.say:
        say_phrase(args.say)

# Parse args
def parse_args():
    parser = argparse.ArgumentParser(description="Get or set the speaker volume.")
    parser.add_argument("percent", nargs="?", type=int, help="Volume percent to set, leave out to print the current volume")
    parser.add_argument("--say", default="", help="Phrase to speak afterwards, like Hi")
    return parser.parse_args()

# Play a phrase in the talk voice, making its wav with kokoro the first time
def say_phrase(text):
    import sys
    sys.path.insert(0, TALK_DIR)
    import utils

    # Make the wav once, then reuse it
    name = SAY_WAV_PREFIX + re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") + ".wav"
    wav_path = os.path.join(utils.AUDIO_DIR, name)
    if not os.path.isfile(wav_path):
        make_phrase_wav(utils, text, wav_path)

    # Play through the shared speaker device so talk can still speak
    subprocess.run(utils.play_wav_command(wav_path), capture_output=True)

# Generate one phrase with kokoro on the cpu and write it as a wav
def make_phrase_wav(utils, text, wav_path):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    utils.enable_offline_if_cached(utils.DEFAULT_VOICE)
    utils.configure_torch_threads()
    utils.suppress_torch_warnings()
    import kokoro
    import numpy
    import soundfile

    # Load the voice, then join every chunk the pipeline yields
    model = kokoro.KModel(repo_id=utils.REPO_ID, disable_complex=True).eval()
    pipeline = kokoro.KPipeline(lang_code=utils.DEFAULT_VOICE[0], repo_id=utils.REPO_ID, model=model)
    chunks = [audio for _, _, audio in pipeline(text, voice=utils.DEFAULT_VOICE, speed=SAY_SPEED)]
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    soundfile.write(wav_path, numpy.concatenate(chunks), SAY_SAMPLE_RATE)

# Spoken confirmation after a successful volume set
def confirm_volume_set():
    if last_volume_percent is None:
        return None
    return VOLUME_SET_REPLY.format(percent=last_volume_percent)

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
    return VOLUME_SET_REPLY.format(percent=percent)

# Set speaker volume from tool arguments
def run_set_volume(arguments):
    global last_volume_percent

    # Mic stays at the standard level no matter where the speaker is set
    set_microphone()
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
    return VOLUME_SET_REPLY.format(percent=percent)

# Read speaker volume for the tool
def run_get_volume(arguments=None):
    # Opening the volume picker applies the mic level too
    set_microphone()
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

# Bring both mic sliders to the level alsamixer draws as the standard percent
def set_microphone():
    # Same card as the speaker
    card = find_volume_card()
    if card is None:
        return

    # Playback is the slider alsamixer opens on, capture is the recording gain
    set_displayed_volume(card, MIC_PLAYBACK_VOLUME)
    set_displayed_volume(card, MIC_CAPTURE_VOLUME)

    # Playback switch is sidetone, capture switch is the mic itself
    set_switch(card, MIC_PLAYBACK_SWITCH, "off")
    set_switch(card, MIC_CAPTURE_SWITCH, "on")
    set_auto_gain(card)

# Set one volume so alsamixer shows MIC_CAPTURE_PERCENT
def set_displayed_volume(card, name):
    raw = raw_for_displayed_percent(card, name)
    if raw is None:
        return
    subprocess.run(["amixer", "-c", str(card), "cset", f"name={name}", str(raw)], capture_output=True, text=True)

# Set a mixer switch, missing controls are ignored
def set_switch(card, name, value):
    subprocess.run(["amixer", "-c", str(card), "cset", f"name={name}", value], capture_output=True, text=True)

# Return the raw step whose log curve lands on the standard percent
def raw_for_displayed_percent(card, name):
    limits = control_limits(card, name)
    if limits is None:
        return None
    raw_min, raw_max, db_min, db_max = limits

    # No dB range, the raw step is already the percent alsamixer shows
    if db_min is None or db_max is None or db_max <= db_min:
        return int(round(raw_min + (MIC_CAPTURE_PERCENT / 100.0) * (raw_max - raw_min)))

    # Narrow spans are a straight line, wide ones are the alsamixer log curve
    if db_max - db_min <= MIC_LINEAR_DB_SPAN:
        db = db_min + (MIC_CAPTURE_PERCENT / 100.0) * (db_max - db_min)
    else:
        minimum_norm = 10 ** ((db_min - db_max) / 60.0)
        mapped = (MIC_CAPTURE_PERCENT / 100.0) * (1 - minimum_norm) + minimum_norm
        db = 60.0 * math.log10(mapped) + db_max
    share = (db - db_min) / (db_max - db_min)
    return int(round(raw_min + share * (raw_max - raw_min)))

# Return raw min, raw max, dB min, dB max for one mixer control
def control_limits(card, name):
    result = subprocess.run(["amixer", "-c", str(card), "contents"], capture_output=True, text=True)
    for block in result.stdout.split("numid="):
        if f"name='{name}'" not in block:
            continue
        minimum = re.search(r"min=(-?\d+),max=(-?\d+)", block)
        if not minimum:
            return None
        raw_min = int(minimum.group(1))
        raw_max = int(minimum.group(2))
        decibels = re.search(r"dBminmax-min=(-?[0-9.]+)dB,max=(-?[0-9.]+)dB", block)
        if not decibels:
            return raw_min, raw_max, None, None
        return raw_min, raw_max, float(decibels.group(1)), float(decibels.group(2))
    return None

# Turn on the dongle auto gain switch when the card has one
def set_auto_gain(card):
    subprocess.run(["amixer", "-c", str(card), "set", AGC_CONTROL, "on"], capture_output=True, text=True)

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
