#!.venv/bin/python

# Imports
import os
import platform
import shutil
import subprocess
import sys
import time
import warnings

import numpy as np

# Config wake and listen
WAKE_WORD = 'robot'
GREETING = 'Hi!'
WHISPER_MODEL_SIZE = 'base'
SAMPLE_RATE = 16000
CHUNK_SECONDS = 2
COMMAND_SECONDS = 5
FAKE_WAKE_SECONDS = 10
MAC_RECORDER = 'rec'
LINUX_RECORDER = 'arecord'

# Config voice
REPO_ID = 'hexgrad/Kokoro-82M'
VOICE = 'bm_fable'
SPEECH_SPEED = 1.2
MAC_PLAYER = 'afplay'
LINUX_PLAYER = 'aplay'

# Config dirs and env
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')
os.environ['HF_HUB_CACHE'] = CACHE_DIR
os.environ['HF_HUB_VERBOSITY'] = 'error'

# State
TEST_MODE = False
FAKE_MODE = False

# Main
def main():
    # Parse args
    global TEST_MODE, FAKE_MODE
    TEST_MODE, FAKE_MODE = parse_args()

    # Build the record command, quits when no microphone is available
    record = record_command()

    # Quit early when audio playback is unavailable
    check_ready()

    # Silence onnxruntime GPU discovery warning from the VAD
    import_onnxruntime_quietly()

    # Load models
    whisper_model = load_whisper_model()
    kokoro_pipeline = load_kokoro_pipeline()

    # Speak one test exchange or run the wake word loop
    if TEST_MODE:
        run_test(kokoro_pipeline)
    else:
        run_talk_loop(whisper_model, kokoro_pipeline, record)

# Parse command line arguments
def parse_args():
    test_mode = False
    fake_mode = False
    for argument in sys.argv[1:]:
        if argument == '--test':
            test_mode = True
        elif argument == '--fake':
            fake_mode = True
        else:
            print(f"Unknown argument: {argument}")
            sys.exit(1)
    return test_mode, fake_mode

# Load whisper model on gpu when available
def load_whisper_model():
    print(f'Loading whisper {WHISPER_MODEL_SIZE} model...', flush=True)
    import ctranslate2
    from faster_whisper import WhisperModel
    device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'float32'
    model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)
    print(f'Whisper {WHISPER_MODEL_SIZE} on {device.upper()}')
    return model

# Load kokoro speech pipeline on gpu when available
def load_kokoro_pipeline():
    # Suppress torch warnings before kokoro imports torch
    print('Loading kokoro model...', flush=True)
    os.environ['OMP_NUM_THREADS'] = '4'
    os.environ['MKL_NUM_THREADS'] = '4'
    os.environ['OPENBLAS_NUM_THREADS'] = '4'
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.nn.modules.rnn')
    warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.cuda')

    # Import kokoro and pick device
    global kokoro, torch, soundfile
    import kokoro
    import torch
    import soundfile
    torch.set_num_threads(4)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    # Load model and voice
    model = kokoro.KModel(repo_id=REPO_ID, disable_complex=True).to(device).eval()
    pipeline = kokoro.KPipeline(lang_code=VOICE[0], repo_id=REPO_ID, model=model)
    pipeline.load_voice(VOICE)
    print(f'Kokoro on {device.upper()}, voice {VOICE}')
    return pipeline

# Speak one canned exchange and exit
def run_test(kokoro_pipeline):
    command = 'what time is it'
    print(f'Command: {command}')
    reply = make_reply(command)
    print(f'Reply: {reply}')
    speak(kokoro_pipeline, reply)

# Listen for the wake word, then a command, then reply
def run_talk_loop(whisper_model, kokoro_pipeline, record):
    # Greet, then start listening
    speak(kokoro_pipeline, GREETING)
    print(f'Say "{WAKE_WORD}" to talk, CTRL-C to stop.', flush=True)
    recorder = start_recorder(record)
    chunk_bytes = SAMPLE_RATE * 2 * CHUNK_SECONDS
    listen_start = time.monotonic()
    try:
        while True:
            # Listen for the wake word
            data = recorder.stdout.read(chunk_bytes)
            if not data:
                break
            text = transcribe(whisper_model, data)
            if text:
                print(f'Heard: {text}', flush=True)

            # Pretend to hear the wake word in fake mode
            if FAKE_MODE and time.monotonic() - listen_start > FAKE_WAKE_SECONDS:
                print(f'Fake wake after {FAKE_WAKE_SECONDS}s', flush=True)
            elif WAKE_WORD not in text.lower():
                continue

            # Acknowledge, mic off while speaking
            recorder.terminate()
            speak(kokoro_pipeline, 'Yes?')

            # Record the command
            recorder = start_recorder(record)
            command_bytes = SAMPLE_RATE * 2 * COMMAND_SECONDS
            data = recorder.stdout.read(command_bytes)
            command = transcribe(whisper_model, data)
            recorder.terminate()
            print(f'Command: {command}', flush=True)

            # Reply and listen again
            reply = make_reply(command)
            print(f'Reply: {reply}', flush=True)
            speak(kokoro_pipeline, reply)
            recorder = start_recorder(record)
            listen_start = time.monotonic()
    except KeyboardInterrupt:
        print('\nDone.')
    finally:
        recorder.terminate()

# Transcribe raw pcm audio to text
def transcribe(whisper_model, data):
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = whisper_model.transcribe(audio, language='en', vad_filter=True)
    return ' '.join(segment.text.strip() for segment in segments).strip()

# Build a reply for the command
def make_reply(command):
    lowered = command.lower()

    # No speech heard
    if not lowered:
        return "I didn't catch that."

    # Simple built in replies
    if 'time' in lowered:
        return time.strftime('It is %I:%M %p.')
    if 'date' in lowered or 'day' in lowered:
        return time.strftime('It is %A, %B %d.')
    if 'your name' in lowered or 'who are you' in lowered:
        return "I am robot."
    if 'hello' in lowered or 'hi ' in lowered or lowered == 'hi':
        return 'Hello there!'
    if 'how are you' in lowered:
        return "I'm doing great, thank you!"
    if 'thank' in lowered:
        return "You're welcome!"

    # Echo anything else
    return f'You said: {command}'

# Generate speech and play it on the usb speaker
def speak(kokoro_pipeline, text):
    os.makedirs(AUDIO_DIR, exist_ok=True)
    generator = kokoro_pipeline(text, voice=VOICE, speed=SPEECH_SPEED)
    for index, (graphemes, phonemes, audio) in enumerate(generator):
        wav_path = os.path.join(AUDIO_DIR, 'talk.wav')
        soundfile.write(wav_path, audio, 24000)
        subprocess.run(play_wav_command(wav_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Import onnxruntime with stderr muted, it warns during gpu discovery on jetson
def import_onnxruntime_quietly():
    stderr_fd = sys.stderr.fileno()
    saved_fd = os.dup(stderr_fd)
    with open(os.devnull, 'w') as devnull:
        os.dup2(devnull.fileno(), stderr_fd)
        try:
            import onnxruntime
            onnxruntime.set_default_logger_severity(3)
        finally:
            os.dup2(saved_fd, stderr_fd)
            os.close(saved_fd)

# Build the record command for this platform
def record_command():
    if platform.system() == 'Darwin':
        return mac_record_command()
    return linux_record_command()

# Record from the default input device with sox
def mac_record_command():
    if shutil.which(MAC_RECORDER) is None:
        print('sox not found. Run ./install.sh --listen to install it.')
        sys.exit(1)
    return [MAC_RECORDER, '-q', '-t', 'raw', '-b', '16', '-e', 'signed-integer', '-c', '1', '-r', str(SAMPLE_RATE), '-']

# Record from the first usb microphone with arecord
def linux_record_command():
    card = find_capture_card()
    if card is None:
        print('No microphone found. Plug in a USB mic and try again.')
        sys.exit(1)
    return [LINUX_RECORDER, '-D', f'plughw:{card},0', '-f', 'S16_LE', '-r', str(SAMPLE_RATE), '-c', '1', '-t', 'raw', '-q']

# Return card index for the first device with capture support
def find_capture_card():
    result = subprocess.run([LINUX_RECORDER, '-l'], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith('card') and 'USB' in line:
            return int(line.split(':')[0].split()[1])
    return None

# Start the recorder streaming raw audio to stdout
def start_recorder(command):
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

# Quit when audio playback is unavailable
def check_ready():
    player = audio_player()
    if shutil.which(player) is None:
        print(f'Audio playback unavailable: {player} not found.')
        sys.exit(1)
    if player == LINUX_PLAYER and find_usb_card() is None:
        print('No USB speaker found. Plug one in and run ./tools-audio.sh.')
        sys.exit(1)

# Return true when a card has a capture stream
def card_has_capture(card_index):
    stream_path = f'/proc/asound/card{card_index}/stream0'
    if not os.path.isfile(stream_path):
        return False
    with open(stream_path) as stream_file:
        return 'Capture:' in stream_file.read()

# Return card index for the playback-only USB sound device
def find_usb_card():
    cards_path = '/proc/asound/cards'
    if not os.path.isfile(cards_path):
        return None

    # Collect USB card indexes
    usb_cards = []
    with open(cards_path) as cards_file:
        for line in cards_file:
            if 'USB-Audio' not in line:
                continue
            card_index_text = line.strip().split(None, 1)[0]
            if card_index_text.isdigit():
                usb_cards.append(int(card_index_text))

    # Prefer the speaker-only card, one without a mic
    for card_index in usb_cards:
        if not card_has_capture(card_index):
            return card_index
    if usb_cards:
        return usb_cards[0]
    return None

# Build playback command for one wav file
def play_wav_command(wav_path):
    player = audio_player()
    if player == MAC_PLAYER:
        return [player, wav_path]
    card = find_usb_card()
    if card is None:
        return [player, wav_path]
    return [player, '-D', f'plughw:{card},0', wav_path]

# Return audio player command for this platform
def audio_player():
    return MAC_PLAYER if platform.system() == 'Darwin' else LINUX_PLAYER

# Main
if __name__ == '__main__':
    main()
