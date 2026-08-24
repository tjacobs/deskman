#!.venv/bin/python

# Imports
import os
import platform
import shutil
import subprocess
import sys

import numpy as np

# Config
MODEL_SIZE = 'base'
SAMPLE_RATE = 16000
CHUNK_SECONDS = 3
MAC_RECORDER = 'rec'
LINUX_RECORDER = 'arecord'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
os.environ['HF_HUB_CACHE'] = CACHE_DIR
os.environ['HF_HUB_VERBOSITY'] = 'error'

# Main
def main():
    # Show help or reject unknown arguments
    parse_args()

    # Build the record command, quits when no microphone is available
    command = record_command()

    # Silence onnxruntime GPU discovery warning from the VAD
    import_onnxruntime_quietly()

    # Load model and transcribe the microphone until stopped
    model = load_model()
    run_transcribe_loop(model, command)

# Parse command line arguments
def parse_args():
    for argument in sys.argv[1:]:
        if argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f"Unknown argument: {argument}")
            print_usage()
            sys.exit(1)

# Print usage help
def print_usage():
    print('Usage: ./speech_listen.py')
    print(f'  (no arg)  transcribe the microphone with the whisper {MODEL_SIZE} model, CTRL-C to stop')

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

# Return card index for the USB microphone
def find_capture_card():
    # Collect every USB card that can record
    result = subprocess.run([LINUX_RECORDER, '-l'], capture_output=True, text=True)
    usb_cards = []
    for line in result.stdout.splitlines():
        if line.startswith('card') and 'USB' in line:
            usb_cards.append(int(line.split(':')[0].split()[1]))

    # Prefer the mic-only card, a speaker card records through a poor fallback mic
    for card_index in usb_cards:
        if not card_has_playback(card_index):
            return card_index

    # Otherwise take the first one
    if usb_cards:
        return usb_cards[0]
    return None

# Return true when a card has a playback stream
def card_has_playback(card_index):
    stream_path = f'/proc/asound/card{card_index}/stream0'
    if not os.path.isfile(stream_path):
        return False
    with open(stream_path) as stream_file:
        return 'Playback:' in stream_file.read()

# Import onnxruntime with stderr muted, it warns during gpu discovery on jetson
def import_onnxruntime_quietly():
    stderr_fd = sys.stderr.fileno()
    saved_fd = os.dup(stderr_fd)
    with open(os.devnull, 'w') as devnull:
        os.dup2(devnull.fileno(), stderr_fd)
        try:
            import onnxruntime
            onnxruntime.set_default_logger_severity(3)
            import_failed = False
        except ImportError:
            import_failed = True
        finally:
            os.dup2(saved_fd, stderr_fd)
            os.close(saved_fd)

    # Quit with install help when the listen deps are missing
    if import_failed:
        print('onnxruntime not found. Run ./install.sh --listen to install it.')
        sys.exit(1)

# Load whisper model on gpu when available
def load_model():
    # Pick device
    print(f'Loading {MODEL_SIZE} model...', flush=True)
    import ctranslate2
    from faster_whisper import WhisperModel
    device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'float32'

    # Load model
    model = WhisperModel(MODEL_SIZE, device=device, compute_type=compute_type)
    label = 'GPU' if device == 'cuda' else device.upper()
    print(f'Whisper {MODEL_SIZE} on {label} {compute_type}, VAD on CPU')
    return model

# Record chunks from the microphone and print transcripts
def run_transcribe_loop(model, command):
    recorder = start_recorder(command)
    chunk_bytes = SAMPLE_RATE * 2 * CHUNK_SECONDS
    print('Listening, speak now, CTRL-C to stop.', flush=True)
    try:
        while True:
            # Read one chunk of raw audio
            data = recorder.stdout.read(chunk_bytes)
            if not data:
                break

            # Transcribe and print each spoken segment
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = model.transcribe(audio, language='en', vad_filter=True)
            for segment in segments:
                print(segment.text.strip(), flush=True)
    except KeyboardInterrupt:
        print('\nDone.')
    finally:
        recorder.terminate()

    # Suggest the full talk loop next
    print('Next: put it all together with ./talk.py.')

# Start the recorder streaming raw audio to stdout
def start_recorder(command):
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

# Main
if __name__ == '__main__':
    main()
