#!/usr/bin/env python3

# Ask the running talk.py to render spoken lines to wav files, so its kokoro is reused and none loads here

# Imports
import json
import os
import sys
import time
import uuid

# Import the talk helpers for the spool folder that talk.py watches
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils

# Config the defaults used with no args
DEFAULT_VOICE = 'am_adam'
DEFAULT_WAV = '/tmp/render_line.wav'
DEFAULT_TEXT = 'Deskman. What is the time?'

# Config how long to wait for talk.py to render each line
RENDER_WAIT_SECONDS = 120
RENDER_POLL_SECONDS = 0.2

# Main
def main():
    # Read the voice and the wav and text pairs
    voice, pairs = parse_args()

    # Send every request first, so talk.py renders them back to back
    for wav_path, text in pairs:
        send_request(voice, wav_path, text)

    # Wait for every wav to appear
    for wav_path, _ in pairs:
        wait_for_wav(wav_path)

# Parse args, the voice then pairs of wav path and text
def parse_args():
    # Use the defaults when nothing is passed
    arguments = sys.argv[1:]
    if not arguments:
        return DEFAULT_VOICE, [(DEFAULT_WAV, DEFAULT_TEXT)]

    # Show help when requested
    if arguments[0] in ('-h', '--help') or len(arguments) % 2 == 0:
        print('Usage: render_line.py voice wav_path text [wav_path text ...]')
        sys.exit(0)

    # Pair up the wav paths and texts
    rest = arguments[1:]
    return arguments[0], list(zip(rest[0::2], rest[1::2]))

# Write one request into the spool folder, renamed into place so talk.py never reads half of it
def send_request(voice, wav_path, text):
    # Remove an old wav so a stale file is not mistaken for the new one
    os.makedirs(utils.RENDER_DIR, exist_ok=True)
    if os.path.exists(wav_path):
        os.remove(wav_path)

    # Write the request under a temporary name, then rename it
    name = uuid.uuid4().hex
    temp_path = os.path.join(utils.RENDER_DIR, name + '.tmp')
    with open(temp_path, 'w') as save:
        json.dump({'voice': voice, 'wav': wav_path, 'text': text}, save)
    os.replace(temp_path, os.path.join(utils.RENDER_DIR, name + '.json'))

# Wait until talk.py has written the wav
def wait_for_wav(wav_path):
    # Poll until the file appears or the wait runs out
    deadline = time.time() + RENDER_WAIT_SECONDS
    while time.time() < deadline:
        if os.path.exists(wav_path):
            return
        time.sleep(RENDER_POLL_SECONDS)

    # Fail so the caller knows talk.py is not rendering
    sys.exit(f'Error: talk.py did not render {wav_path}, is it running the latest code?')

# Main
if __name__ == '__main__':
    main()
