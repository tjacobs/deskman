#!.venv/bin/python

# Speech_elevenlabs.py speaks text in an ElevenLabs voice, streaming the audio to the speaker as it arrives.

# Imports
import os
import json
import argparse
import urllib.error
import urllib.request

# Imports from the shared voice helper, which also puts the text helper on the path
import voice
import utils
import robot_move

# Config the speech, config.json overrides these
ELEVENLABS_MODEL = 'eleven_v3'
ELEVENLABS_VOICE = 'george'
ELEVENLABS_STABILITY = 0.5
ELEVENLABS_STYLE = 0.0
ELEVENLABS_SPEED = 1.0

# Config the connection
SPEECH_URL = 'https://api.elevenlabs.io/v1/text-to-speech'
SPEECH_FORMAT = 'pcm_24000'
CONNECT_TIMEOUT = 30
CHUNK_BYTES = 4800

# Config where the API key is read from when the environment has none
KEY_NAME = 'ELEVENLABS_API_KEY'
KEY_FILE = os.path.join(utils.SCRIPT_DIR, 'elevenlabs.env')

# Config the premade voices to pick from and how each comes across, mapped to their ElevenLabs voice ids
ELEVENLABS_VOICES = {
    'george': ('JBFqnCBsd6RMkjVDRZzb', 'male'), 'brian': ('nPczCjzI2devNBz1zQrb', 'male'), 'daniel': ('onwK4e9ZLuTAKqWW03F9', 'male'), 'charlie': ('IKne3meq5aSn9XLyUdCD', 'male'),
    'sarah': ('EXAVITQu4vr4xnSDxMaL', 'female'), 'alice': ('Xb7hH8MSUJpSbSDYk0k2', 'female'), 'lily': ('pFZP5JQG7iQjIQuC4Bku', 'female'), 'jessica': ('cgSgspJ2msm6clMCkdW9', 'female')}

# Config the models that read emotion tags in the text, other models are shaped by the voice settings only
TAG_MODELS = ('eleven_v3',)

# Config what the text model is told so it writes the tags the voice can act on
EXPRESSION_STYLE = 'Your voice reads emotion tags in square brackets, like [whispers], [laughs], [sighs], [excited], or [curious]. Use at most one in a reply, put it just before the words it colours, and only when it fits. Never explain the tag.'

# Config the short names shown on the face for ElevenLabs failures, checked in order
ELEVENLABS_KEY_REJECTED = 'ElevenLabs key rejected'
ELEVENLABS_OUT_OF_QUOTA = 'ElevenLabs out of quota'
ELEVENLABS_ERROR_NAMES = (
    (('401', '403', 'unauthorized', 'invalid_api_key'), ELEVENLABS_KEY_REJECTED),
    (('402', '429', 'quota', 'payment'), ELEVENLABS_OUT_OF_QUOTA))
ELEVENLABS_ERROR_NAME = 'ElevenLabs error'

# State, the voice a set_voice call picked, it holds for the rest of the run
CHOSEN_VOICE = ''

# State, the speaker playing right now and whether it was told to stop
speaker = None
stopped = False

# Speak one line in the ElevenLabs voice
def main():
    # Parse arguments
    arguments = parse_args()

    # Load from the command line, overriding config.json
    config = load_config()
    if arguments.model:
        config['elevenlabs_model'] = arguments.model
    if arguments.voice:
        config['elevenlabs_voice'] = arguments.voice

    # Speak the line
    try:
        speak(arguments.text, config)
    except Exception as error:
        raise SystemExit(f'ElevenLabs failed: {error}')

# Parse command line arguments
def parse_args():
    # Describe the arguments
    parser = argparse.ArgumentParser(description='Speak a line with ElevenLabs.')
    parser.add_argument('text', nargs='?', default='Hello, I am Deskman. [excited] It is great to meet you!', help='the line to speak')
    parser.add_argument('--model', help='ElevenLabs model to use, overrides config.json')
    parser.add_argument('--voice', help='voice to speak with, a name from the list or a voice id, overrides config.json')
    return parser.parse_args()

# Read config.json, falling back to the defaults above for anything missing
def load_config():
    # Load the settings with the defaults above
    config = utils.load_config({'elevenlabs_model': ELEVENLABS_MODEL, 'elevenlabs_voice': ELEVENLABS_VOICE, 'elevenlabs_stability': ELEVENLABS_STABILITY, 'elevenlabs_style': ELEVENLABS_STYLE, 'elevenlabs_speed': ELEVENLABS_SPEED})

    # Prefer a voice a set_voice call picked
    if CHOSEN_VOICE:
        config['elevenlabs_voice'] = CHOSEN_VOICE
    return config

# Read the API key from the environment, or from talk/elevenlabs.env
def load_key():
    # Prefer the environment
    key = os.environ.get(KEY_NAME, '').strip()
    if key or not os.path.isfile(KEY_FILE):
        return key

    # Read the key line from the file
    with open(KEY_FILE, encoding='utf-8') as key_file:
        for line in key_file:
            if line.strip().startswith(KEY_NAME + '='):
                return line.split('=', 1)[1].strip().strip('\'"')
    return ''

# What the text model is told about tags, empty when the model cannot read them
def speech_style():
    return EXPRESSION_STYLE if load_config()['elevenlabs_model'] in TAG_MODELS else ''

# Speak the text and return once it has played, or raise when ElevenLabs turns it down
def speak(text, config):
    # Say nothing at bedtime
    global speaker, stopped
    if utils.is_bedtime():
        print(f'Bedtime, not saying: {text}', flush=True)
        return

    # Open the request on the chosen voice
    response = open_stream(text, config)

    # Play each piece as it arrives, keeping whole samples, and stop the moment mute is on
    stopped = False
    speaker = voice.Speaker()
    leftover = b''
    try:
        while not stopped and not robot_move.muted:
            data = response.read(CHUNK_BYTES)
            if not data:
                break
            data = leftover + data
            leftover = data[len(data) // 2 * 2:]
            speaker.play(data[:len(data) // 2 * 2])

        # Let it finish playing, unless it was cut off
        if not stopped and not robot_move.muted:
            speaker.drain()
    finally:
        response.close()
        speaker.stop()

# Send the text to ElevenLabs and return the open audio stream
def open_stream(text, config):
    # Get the API key
    key = load_key()
    if not key:
        raise RuntimeError('No ElevenLabs key, set ELEVENLABS_API_KEY or write talk/elevenlabs.env.')

    # Describe the voice and the model, a name from the list or a voice id both work
    voice_id = ELEVENLABS_VOICES.get(config['elevenlabs_voice'], (config['elevenlabs_voice'], ''))[0]
    settings = {'stability': config['elevenlabs_stability'], 'style': config['elevenlabs_style'], 'speed': config['elevenlabs_speed']}
    body = json.dumps({'text': text, 'model_id': config['elevenlabs_model'], 'voice_settings': settings}).encode()

    # Open the audio stream
    request = urllib.request.Request(f'{SPEECH_URL}/{voice_id}/stream?output_format={SPEECH_FORMAT}', data=body, headers={'xi-api-key': key, 'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'{error.code} {error.read().decode(errors="replace")[:200]}')

# Cut off the speech now, so mute silences the robot at once
def stop():
    global stopped
    stopped = True
    if speaker is not None:
        speaker.stop()

# Name an ElevenLabs failure in a few words for the face, the full message stays in the log
def describe_error(error):
    # Find the first name whose phrases match
    lowered = str(error).lower()
    for phrases, name in ELEVENLABS_ERROR_NAMES:
        if any(phrase in lowered for phrase in phrases):
            return name
    return ELEVENLABS_ERROR_NAME

# Change the speaking voice to a name from the list
def set_voice(voice_name):
    # Match the name
    global CHOSEN_VOICE
    name = str(voice_name or '').strip().lower()
    if name not in ELEVENLABS_VOICES:
        return f'Unknown voice: {voice_name}. Available: {", ".join(ELEVENLABS_VOICES)}.'

    # Keep it for the rest of the run
    CHOSEN_VOICE = name
    print(f'Voice set to {name}', flush=True)
    return f'Voice set to {name}.'

# List the voices grouped by how they sound
def list_voices(count):
    # Take the first few when asked
    names = list(ELEVENLABS_VOICES)
    if count is not None:
        names = names[:max(1, min(len(names), int(count)))]
    sounds = ', '.join(f'{name} sounds {ELEVENLABS_VOICES[name][1]}' for name in names)
    return f'Current voice {load_config()["elevenlabs_voice"]}. Voices: {sounds}.'


# Run main
if __name__ == '__main__':
    main()
