#!.venv/bin/python

# Imports
import os
import sys
import time
import queue
import shutil
import warnings
import platform
import threading
import subprocess
import collections
import numpy as np

# Config wake and listen
WAKE_WORD = 'robot'
GREETING = 'Hi!'
ACKNOWLEDGEMENT = 'Yes?'
TEST_QUESTION = 'What is the time?'
WHISPER_MODEL_SIZE = 'base'
SAMPLE_RATE = 16000
MAC_RECORDER = 'rec'
LINUX_RECORDER = 'arecord'

# Config speech detection, silero reads 512 sample frames
VAD_FRAME_SAMPLES = 512
VAD_BLOCK_FRAMES = 8
VAD_THRESHOLD = 0.5
PRE_ROLL_SECONDS = 0.5
SILENCE_END_SECONDS = 0.7
MIN_SPEECH_SECONDS = 0.3
MAX_UTTERANCE_SECONDS = 15

# Config block sizes worked out from the frame size
BLOCK_SAMPLES = VAD_FRAME_SAMPLES * VAD_BLOCK_FRAMES
BLOCK_BYTES = BLOCK_SAMPLES * 2
BLOCK_SECONDS = BLOCK_SAMPLES / SAMPLE_RATE
PRE_ROLL_BLOCKS = round(PRE_ROLL_SECONDS / BLOCK_SECONDS)
SILENCE_END_BLOCKS = round(SILENCE_END_SECONDS / BLOCK_SECONDS)
MIN_SPEECH_BLOCKS = round(MIN_SPEECH_SECONDS / BLOCK_SECONDS)
MAX_UTTERANCE_BLOCKS = round(MAX_UTTERANCE_SECONDS / BLOCK_SECONDS)

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

# Main
def main():
    # Parse args
    global TEST_MODE
    TEST_MODE = parse_args()

    # Build the record command, quits when no microphone is available
    record = record_command()

    # Quit early when audio playback is unavailable
    check_ready()

    # Silence onnxruntime GPU discovery warning from the VAD
    import_onnxruntime_quietly()

    # Load models
    whisper_model = load_whisper_model()
    vad_model = load_vad_model()
    kokoro_pipeline = load_kokoro_pipeline()

    # Listen continuously, test mode does one exchange and exits
    listener = Listener(record, vad_model)
    try:
        run_talk_loop(whisper_model, kokoro_pipeline, listener)
    finally:
        listener.stop()

# Parse command line arguments
def parse_args():
    test_mode = False
    for argument in sys.argv[1:]:
        if argument == '--test':
            test_mode = True
        else:
            print(f"Unknown argument: {argument}")
            sys.exit(1)
    return test_mode

# Listen for the wake word, then a command, then reply
def run_talk_loop(whisper_model, kokoro_pipeline, listener):
    # Greet, then start listening
    speak_muted(listener, kokoro_pipeline, GREETING)
    print_talk_help()
    try:
        while True:
            # Ask itself the test question, mic stays on so it hears itself
            if TEST_MODE:
                speak(kokoro_pipeline, TEST_QUESTION)
                command = hear_command(whisper_model, listener, TEST_QUESTION)

            # Wait for the wake word, then take the rest of what was said
            else:
                command = hear_wake_command(whisper_model, kokoro_pipeline, listener)
            if command is None:
                break
            print(f'Command: {command}', flush=True)

            # Reply, mic muted so it does not hear itself
            reply = make_reply(command)
            print(f'Reply: {reply}', flush=True)
            speak_muted(listener, kokoro_pipeline, reply)
            if TEST_MODE:
                print('Test done.')
                break
    except KeyboardInterrupt:
        print('\nDone.')

# Load whisper model on gpu when available
def load_whisper_model():
    print('Loading whisper...', flush=True)
    load_start = time.perf_counter()
    import ctranslate2
    from faster_whisper import WhisperModel
    device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
    compute_type = 'float16' if device == 'cuda' else 'float32'
    model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)
    print(f'Loaded on {device.upper()} in {time.perf_counter() - load_start:.1f} sec')
    return model

# Load the silero speech detector bundled with faster-whisper
def load_vad_model():
    from faster_whisper.vad import get_vad_model
    return get_vad_model()

# Load kokoro speech pipeline on gpu when available
def load_kokoro_pipeline():
    # Suppress torch warnings before kokoro imports torch
    print('Loading kokoro...', flush=True)
    load_start = time.perf_counter()
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
    print(f'Loaded on {device.upper()} in {time.perf_counter() - load_start:.1f} sec, voice {VOICE}')
    return pipeline

# Print how to talk, test mode skips the wake word
def print_talk_help():
    if TEST_MODE:
        print(f'Test mode, asking itself "{TEST_QUESTION}" and answering.', flush=True)
    else:
        print(f'Say "{WAKE_WORD}" to talk, CTRL-C to stop.', flush=True)

# Wait for the wake word, then return the command that follows it
def hear_wake_command(whisper_model, kokoro_pipeline, listener):
    while True:
        # Transcribe one whole utterance
        audio = listener.next_utterance()
        if audio is None:
            return None
        text = transcribe(whisper_model, audio)
        if text:
            print(f'Heard: {text}', flush=True)

        # Keep listening until the wake word turns up
        if WAKE_WORD not in text.lower():
            continue

        # Use the rest of the utterance, or acknowledge and wait for more
        command = text_after_wake(text)
        if command:
            return command
        speak_muted(listener, kokoro_pipeline, ACKNOWLEDGEMENT)
        return hear_command(whisper_model, listener, '')

# Transcribe the next utterance, falling back when nothing was heard
def hear_command(whisper_model, listener, fallback):
    audio = listener.next_utterance()
    if audio is None:
        return None
    command = transcribe(whisper_model, audio)
    if not command and fallback:
        print(f'Heard nothing, asking: {fallback}', flush=True)
        return fallback
    return command

# Return what was said after the wake word
def text_after_wake(text):
    position = text.lower().find(WAKE_WORD)
    return text[position + len(WAKE_WORD):].strip(' ,.!?')

# Transcribe audio samples to text
def transcribe(whisper_model, audio):
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
        return f'It is {clock_time()}.'
    if 'date' in lowered or 'day' in lowered:
        return f'It is {calendar_date()}.'
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

# Speak with the mic muted so it does not hear itself
def speak_muted(listener, kokoro_pipeline, text):
    listener.mute()
    speak(kokoro_pipeline, text)
    listener.unmute()

# Generate speech and play it on the usb speaker
def speak(kokoro_pipeline, text):
    os.makedirs(AUDIO_DIR, exist_ok=True)
    generator = kokoro_pipeline(text, voice=VOICE, speed=SPEECH_SPEED)
    for index, (graphemes, phonemes, audio) in enumerate(generator):
        wav_path = os.path.join(AUDIO_DIR, 'talk.wav')
        soundfile.write(wav_path, audio, 24000)
        subprocess.run(play_wav_command(wav_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Keep the microphone open and hand out one utterance at a time
class Listener:
    # Start recording and drain the microphone in the background
    def __init__(self, record, vad_model):
        self.vad_model = vad_model
        self.blocks = queue.Queue()
        self.muted = False
        self.recorder = start_recorder(record)
        self.reader = threading.Thread(target=self.read_blocks, daemon=True)
        self.reader.start()

    # Read blocks until the recorder stops, dropping them while muted
    def read_blocks(self):
        while True:
            data = self.recorder.stdout.read(BLOCK_BYTES)
            if len(data) < BLOCK_BYTES:
                self.blocks.put(None)
                return
            if self.muted:
                continue
            self.blocks.put(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0)

    # Collect audio from when speech starts until it stops
    def next_utterance(self):
        pre_roll = collections.deque(maxlen=PRE_ROLL_BLOCKS)
        utterance = []
        speech_blocks = 0
        silence_blocks = 0
        while True:
            # Stop when the recorder has gone away
            block = self.blocks.get()
            if block is None:
                return None
            speaking = speech_probability(self.vad_model, block) > VAD_THRESHOLD

            # Wait for speech, keeping a little audio from before it started
            if not utterance:
                pre_roll.append(block)
                if speaking:
                    utterance = list(pre_roll)
                    speech_blocks = 1
                continue

            # Collect the utterance and count the silence at the end of it
            utterance.append(block)
            if speaking:
                speech_blocks += 1
                silence_blocks = 0
            else:
                silence_blocks += 1

            # Return once speech has stopped, or the utterance is long enough
            if silence_blocks >= SILENCE_END_BLOCKS or len(utterance) >= MAX_UTTERANCE_BLOCKS:
                if speech_blocks >= MIN_SPEECH_BLOCKS:
                    return np.concatenate(utterance)
                pre_roll.clear()
                utterance = []
                speech_blocks = 0
                silence_blocks = 0

    # Drop microphone audio, used while speaking
    def mute(self):
        self.muted = True

    # Listen again, throwing away anything captured while muted
    def unmute(self):
        self.muted = False
        while not self.blocks.empty():
            self.blocks.get()

    # Stop recording
    def stop(self):
        self.recorder.terminate()

# Return the highest speech probability in one block
def speech_probability(vad_model, block):
    return float(vad_model(block).max())

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

# Format the time without a leading zero, speech reads it as a number
def clock_time():
    now = time.localtime()
    hour = now.tm_hour % 12 or 12
    return f'{hour}:{now.tm_min:02d} {time.strftime("%p", now)}'

# Format the date without a leading zero on the day
def calendar_date():
    now = time.localtime()
    return f'{time.strftime("%A, %B", now)} {now.tm_mday}'

# Main
if __name__ == '__main__':
    main()
