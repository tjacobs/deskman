#!.venv/bin/python

# Imports
import glob
import os
import platform
import queue
import shutil
import subprocess
import sys
import termios
import threading
import time
import tty
import warnings

# Config voice
REPO_ID = 'hexgrad/Kokoro-82M'
DEFAULT_SPEED = 1.5
SPEED_STEP = 0.1
SPEED_MIN = 0.5
SPEED_MAX = 2.0
VOICES = [
    'af_heart', 'af_alloy', 'af_aoede', 'af_bella', 'af_jessica', 'af_kore', 'af_nicole', 'af_nova', 'af_river', 'af_sarah', 'af_sky',
    'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael', 'am_onyx', 'am_puck', 'am_santa',
    'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily',
    'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis',
]
PHRASES = {
    '1': 'hi there',
    '2': 'hello there',
    '3': 'i am a robot!',
    '4': 'beep boop!',
    '5': 'bye for now!',
    '6': 'thank you!',
    '7': 'yes please',
    '8': 'no thanks',
    '9': 'no way!',
}

# Config dirs and env
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
MODEL_CACHE_DIR = os.path.join(CACHE_DIR, 'models--hexgrad--Kokoro-82M', 'snapshots')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')
os.environ['HF_HUB_CACHE'] = CACHE_DIR
os.environ['HF_HUB_VERBOSITY'] = 'error'
os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'

# Config timeouts
LOAD_TIMEOUT_SECONDS = 60
TEST_WAIT_SECONDS = 30

# Config status display
STATUS_STATE_WIDTH = 8
STATUS_QUEUE_WIDTH = 2
STATUS_VOICE_WIDTH = 11
STATUS_SPEED_WIDTH = 4
STATUS_REALTIME_WIDTH = 5

# Config audio player
PLAYER = 'afplay' if platform.system() == 'Darwin' else 'aplay'

# Config device
DEVICE = 'cpu'
FORCE_CPU = False

# State
KOKORO_SECONDS = 0
TEST_MODE = False
OUTPUT_LOCK = threading.Lock()

# Main
def main():
    # Check offline cache
    #check_offline_cache()

    # Load kokoro
    init()

    # Set CPU mode to performance, restore when done
    saved_cpu_mode = get_cpu_mode()
    perf_set = set_cpu_mode('performance')
    print_system_info(perf_set)

    # Quit early when audio playback is unavailable
    player_ok, player_error = check_audio_player()
    if not player_ok:
        print(f'Audio playback unavailable: {player_error}')
        sys.exit(1)

    # Start the speech engine
    engine = SpeechEngine()
    engine.start()
    if engine.load_failed:
        sys.exit(1)

    # Run
    try:
        # Run test phrases or handle keyboard input until quit
        if TEST_MODE:
            run_test_loop(engine, PHRASES)
        else:
            run_input_loop(engine, PHRASES)

    # Done
    finally:
        engine.stop()
        if saved_cpu_mode:
            set_cpu_mode(saved_cpu_mode)

# Parse command line arguments
def parse_args():
    force_cpu = False
    test_mode = False
    for argument in sys.argv[1:]:
        if argument == '--cpu':
            force_cpu = True
        elif argument == '--test':
            test_mode = True
        elif argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f"Unknown argument: {argument}")
            print_usage()
            sys.exit(1)
    return force_cpu, test_mode

# Print usage help
def print_usage():
    print('Usage: ./say.py [--cpu] [--test]')
    print('  --cpu     force CPU inference instead of CUDA')
    print('  --test    speak the first two preset phrases, then exit')
    print('  (no arg)  press keys to speak phrases, h shows the controls')

# Import kokoro and configure runtime
def init():
    global FORCE_CPU, TEST_MODE, KOKORO_SECONDS, DEVICE

    # Parse args and configure device flags
    FORCE_CPU, TEST_MODE = parse_args()
    if FORCE_CPU:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''

    # Enable offline mode when cached
    if all_voices_cached():
        os.environ['HF_HUB_OFFLINE'] = '1'

    # Limit torch thread pools before kokoro imports torch
    os.environ['OMP_NUM_THREADS'] = '4'
    os.environ['MKL_NUM_THREADS'] = '4'
    os.environ['OPENBLAS_NUM_THREADS'] = '4'

    # Print banner before heavy imports
    if not TEST_MODE:
        print_banner(PHRASES)

    # Suppress warnings
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.nn.modules.rnn')
    warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.cuda')

    # Import kokoro and pick device
    print("Loading...")
    kokoro_start = time.perf_counter()
    global kokoro, torch, soundfile
    import kokoro
    import torch
    import soundfile
    KOKORO_SECONDS = time.perf_counter() - kokoro_start
    DEVICE = pick_device()
    torch.set_num_threads(4)
    print(f"Import kokoro: {KOKORO_SECONDS:.1f}s")

# Queue two preset phrases and wait for speech to finish
def run_test_loop(engine, phrases):
    keys = sorted(phrases.keys())[:2]
    for key in keys:
        engine.enqueue(phrases[key])
    wait_until_idle(engine)
    write_line("Test done.")

# Handle keyboard commands
def run_input_loop(engine, phrases):
    while True:
        key = read_key()

        # Quit
        if key in ('q', 'Q', '\x03'):
            write_line("Quit.")
            break

        # Custom phrase
        if key in ('t', 'T', ':'):
            text = read_line("Say> ").strip()
            if text:
                engine.set_last_custom(text)
                engine.enqueue(text)
            continue

        # Repeat last custom phrase
        if key in ('r', 'R'):
            text = engine.get_last_custom()
            if text:
                engine.enqueue(text)
                write_status(format_status(engine, f"Repeat: {text}"))
            else:
                write_status(format_status(engine, "No custom phrase to repeat."))
            continue

        # Cancel current speech
        if key in ('c', 'C'):
            if engine.cancel_current():
                write_status(format_status(engine, "Cancelled current speech."))
            continue

        # Clear queue
        if key in ('x', 'X'):
            engine.clear_queue()
            write_status(format_status(engine, "Queue cleared."))
            continue

        # Speed up
        if key in ('+', '='):
            engine.change_speed(SPEED_STEP)
            write_status(format_status(engine, f"Speed {engine.speed:.1f}."))
            continue

        # Speed down
        if key in ('-', '_'):
            engine.change_speed(-SPEED_STEP)
            write_status(format_status(engine, f"Speed {engine.speed:.1f}."))
            continue

        # Next voice
        if key in ('v', 'V'):
            if engine.next_voice():
                write_status(format_status(engine, f"Voice {engine.voice}."))
            continue

        # Help
        if key in ('h', 'H', '?'):
            print_banner(phrases)
            continue

        # Preset phrase keys
        if key in phrases:
            engine.enqueue(phrases[key])
            write_status(format_status(engine, f"Queued: {phrases[key]}"))
            continue

        # Ignore other keys
        if key not in ('\r', '\n'):
            write_status(format_status(engine, f"Unknown key: {repr(key)}"))

# Wait until engine is idle with an empty queue
def wait_until_idle(engine):
    deadline = time.time() + TEST_WAIT_SECONDS
    while time.time() < deadline:
        if engine.state == 'idle' and engine.queue.qsize() == 0 and engine.player_process is None:
            return
        time.sleep(0.1)
    write_line("Test timed out.")
    sys.exit(1)

# Print startup banner and key help
def print_banner(phrases):
    print("Say — interactive speech over SSH")
    print("Preset keys:")
    for key in sorted(phrases.keys()):
        print(f"  {key}  {phrases[key]}")
    print("Controls:")
    print("  t  type a custom phrase")
    print("  r  repeat last custom phrase")
    print("  c  cancel current speech")
    print("  x  clear queued speech")
    print("  +  faster")
    print("  -  slower")
    print("  v  next voice")
    print("  h  show help")
    print("  q  quit")
    print()

# Print cpu, gpu, and device info
def print_system_info(perf_set):
    # Print the cpu name on mac, the scaling mode on linux
    if platform.system() == 'Darwin':
        print(f"CPU: {get_cpu_name()}")
    else:
        current_cpu_mode = get_cpu_mode() or 'unknown'
        if perf_set and current_cpu_mode == 'performance':
            print(f"CPU: {current_cpu_mode}")
        else:
            print(f"CPU: {current_cpu_mode} (run with sudo to change)")

    # Print the gpu and the device in use
    if DEVICE == 'cuda':
        properties = torch.cuda.get_device_properties(0)
        frequency = read_gpu_frequency_mhz()
        clock_text = f"{frequency / 1000:.1f}GHz" if frequency else "unknown"
        memory_gigabytes = properties.total_memory / 1024 / 1024 / 1024
        print(f"GPU: {properties.name}, {memory_gigabytes:.1f}GB memory, clock {clock_text}")
    elif FORCE_CPU:
        print("GPU: disabled")
    else:
        print("GPU: not available")
    print(f"Device: {DEVICE}")

# Format aligned status prefix
def format_status(engine, message, state=None):
    state_label = state if state is not None else engine.state
    if engine.last_realtime_speed is None:
        realtime = f"{'--':>{STATUS_REALTIME_WIDTH}}"
    else:
        realtime = f"{f'{engine.last_realtime_speed:.1f}x':>{STATUS_REALTIME_WIDTH}}"
    prefix = (
        f"[{state_label:<{STATUS_STATE_WIDTH}} | queue {engine.queue.qsize():>{STATUS_QUEUE_WIDTH}} | "
        f"{engine.voice:<{STATUS_VOICE_WIDTH}} | "
        f"{engine.speed:>{STATUS_SPEED_WIDTH}.1f}x | {realtime}]"
    )
    if message:
        return f"{prefix} {message}"
    return prefix

# Read single keypress without waiting for enter
def read_key():
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if not line:
            return 'q'
        return line.strip()[0]

    file_descriptor = sys.stdin.fileno()
    old_settings = termios.tcgetattr(file_descriptor)
    try:
        tty.setraw(file_descriptor)
        key = sys.stdin.read(1)
        if key == '\x1b':
            key += sys.stdin.read(2)
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, old_settings)
    return key

# Read a full line for custom phrases
def read_line(prompt):
    with OUTPUT_LOCK:
        sys.stdout.write('\n' + prompt)
        sys.stdout.flush()
    return input()

# Update status in place on one line
def write_status(text):
    with OUTPUT_LOCK:
        sys.stdout.write('\r\033[K' + text)
        sys.stdout.flush()

# Write a scrolling line to the terminal
def write_line(text):
    with OUTPUT_LOCK:
        sys.stdout.write('\r\033[K' + text + '\n')
        sys.stdout.flush()

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
    # Play through the default device, tools-audio.sh points that at the USB soundcard
    # Naming the card takes it exclusively, which fails whenever pipewire holds it
    return [PLAYER, wav_path]

# Return true when audio player is available
def check_audio_player():
    if shutil.which(PLAYER) is None:
        return False, f'{PLAYER} not found'
    if PLAYER == 'aplay':
        result = subprocess.run(['aplay', '-l'], capture_output=True)
        if result.returncode != 0:
            return False, 'no audio device found'
        if find_usb_card() is None:
            return False, 'no USB audio device found, plug one in and run ./tools-audio.sh'
    return True, None

# Format exception text for status lines
def format_error(error):
    text = str(error).strip()
    if 'offline mode is enabled' in text:
        return 'voice not cached, run say once online to download voices'
    if 'trying to locate the file on the Hub' in text:
        return 'voice not cached, run say once online to download voices'
    if 'Cannot reach' in text:
        return 'network unavailable, voice not cached'
    if len(text) > 80:
        return text[:77] + '...'
    return text

# Read cpu scaling mode for the first core
def get_cpu_mode():
    scaling_file_path = '/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'
    if not os.path.exists(scaling_file_path):
        return None
    with open(scaling_file_path) as scaling_file:
        return scaling_file.read().strip()

# Set cpu scaling mode for all cores, return true on success
def set_cpu_mode(mode):
    for cpu_index in range(64):
        scaling_path = f'/sys/devices/system/cpu/cpu{cpu_index}/cpufreq/scaling_governor'
        if not os.path.exists(scaling_path):
            break
        try:
            with open(scaling_path, 'w') as scaling_file:
                scaling_file.write(mode)
        except OSError:
            return False
    return True

# Read cpu name on mac
def get_cpu_name():
    result = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True)
    return result.stdout.strip() or 'unknown'

# Read jetson gpu clock in mhz from sysfs
def read_gpu_frequency_mhz():
    frequency_paths = glob.glob('/sys/class/devfreq/*gpu*/cur_freq')
    if not frequency_paths:
        return None
    with open(frequency_paths[0]) as frequency_file:
        hertz = int(frequency_file.read().strip())
    return hertz / 1_000_000

# Pick cuda when available, else cpu
def pick_device():
    if FORCE_CPU:
        return 'cpu'
    if torch.cuda.is_available():
        return 'cuda'
    return 'cpu'

# Background speech engine with queue and playback control
class SpeechEngine:

    # Create engine with default voice and speed
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        self.worker = None
        self.running = False
        self.state = 'idle'
        self.player_process = None
        self.cancel_flag = threading.Event()
        self.voice_index = VOICES.index('bm_fable')
        self.voice = VOICES[self.voice_index]
        self.speed = DEFAULT_SPEED
        self.last_realtime_speed = None
        self.model = None
        self.pipeline = None
        self.audio_counter = 1
        self.last_custom = ''
        self.available_voices = set()
        self.pipelines = {}
        self.load_failed = False
        self.load_complete = False
    # Store last typed custom phrase
    def set_last_custom(self, text):
        with self.lock:
            self.last_custom = text

    # Return last typed custom phrase
    def get_last_custom(self):
        with self.lock:
            return self.last_custom

    # Start background worker thread
    def start(self):
        os.makedirs(AUDIO_DIR, exist_ok=True)
        self.running = True
        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()
        print("Loading model...")
        load_start = time.perf_counter()
        self.queue.put(('__load__', None, None, None))
        while not self.load_complete and self.running and not self.load_failed:
            if time.perf_counter() - load_start > LOAD_TIMEOUT_SECONDS:
                self.load_failed = True
                self.running = False
                print(f'\nLoad timed out after {LOAD_TIMEOUT_SECONDS} seconds.')
                return
            time.sleep(0.1)
        if self.load_failed:
            return
        if self.load_complete:
            load_seconds = time.perf_counter() - load_start
            print(f"Load model: {load_seconds:.1f}s")
            with OUTPUT_LOCK:
                sys.stdout.write('\n\r\033[K' + format_status(self, "Ready."))
                sys.stdout.flush()

    # Stop worker and cancel playback
    def stop(self):
        self.running = False
        self.cancel_current()
        self.clear_queue()
        self.queue.put(None)
        if self.worker:
            self.worker.join(timeout=5)

    # Add phrase to speech queue
    def enqueue(self, text):
        with self.lock:
            voice = self.voice
            speed = self.speed
        self.queue.put((text, voice, speed, time.time()))

    # Cancel current generation or playback
    def cancel_current(self):
        was_busy = self.state in ('speaking', 'loading') or self.player_process is not None
        self.cancel_flag.set()
        self.stop_player()
        if self.state == 'speaking':
            self.state = 'idle'
        return was_busy

    # Stop speech, clear queue, and report error
    def handle_error(self, error, context, clear_queue):
        self.cancel_flag.set()
        self.stop_player()
        self.state = 'idle'
        cleared = 0
        if clear_queue:
            cleared = self.clear_queue()
        detail = format_error(error)
        if context:
            write_line(format_status(self, f"{context}: {detail}", 'error'))
        else:
            write_line(format_status(self, detail, 'error'))
        if clear_queue and cleared:
            write_status(format_status(self, 'Queue cleared after error.'))

    # Remove all queued phrases, return count removed
    def clear_queue(self):
        cleared = 0
        while True:
            try:
                item = self.queue.get_nowait()
                if item is None:
                    self.queue.put(None)
                    break
                if item[0] != '__load__':
                    cleared += 1
            except queue.Empty:
                break
        return cleared

    # Change speech speed within limits
    def change_speed(self, delta):
        with self.lock:
            self.speed = max(SPEED_MIN, min(SPEED_MAX, round(self.speed + delta, 2)))

    # Cycle to next voice, return true on success
    def next_voice(self):
        start_index = self.voice_index
        for offset in range(len(VOICES)):
            index = (start_index + 1 + offset) % len(VOICES)
            voice = VOICES[index]
            if self.try_load_voice(voice):
                with self.lock:
                    self.voice_index = index
                    self.voice = voice
                return True
        write_line(format_status(self, 'Voice change failed: no voices available.', 'error'))
        return False

    # Get pipeline for a language code
    def get_pipeline(self, lang_code):
        if self.model is None:
            return None
        if lang_code not in self.pipelines:
            self.pipelines[lang_code] = kokoro.KPipeline(lang_code=lang_code, repo_id=REPO_ID, model=self.model)
        return self.pipelines[lang_code]

    # Try to load a voice, return true when ready
    def try_load_voice(self, voice):
        if voice in self.available_voices:
            return True
        if self.model is None:
            return True
        try:
            pipeline = self.get_pipeline(voice[0])
            pipeline.load_voice(voice)
            self.available_voices.add(voice)
            return True
        except Exception as error:
            write_line(format_status(self, f"Voice {voice} unavailable: {format_error(error)}", 'error'))
            return False

    # Background loop that loads model and speaks queued phrases
    def worker_loop(self):
        while self.running:
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                if item is None:
                    break

                # Load model on first request
                text, voice, speed, queued_at = item
                if text == '__load__':
                    self.load_model()
                    continue

                # Skip stale queue entries
                if queued_at and time.time() - queued_at > 60:
                    continue

                # Speak the phrase
                self.speak_phrase(text, voice, speed)
            except Exception as error:
                self.handle_error(error, 'Worker error', True)

    # Load kokoro model once
    def load_model(self):
        try:
            self.state = 'loading'
            self.model = kokoro.KModel(repo_id=REPO_ID, disable_complex=True).to(DEVICE).eval()
            lang_code = self.voice[0]
            self.pipeline = self.get_pipeline(lang_code)
            self.preload_voices()
            self.state = 'idle'
            self.load_complete = True
        except Exception as error:
            self.model = None
            self.pipeline = None
            self.load_complete = False
            self.load_failed = True
            self.running = False
            print(f'\nModel load failed: {format_error(error)}')

    # Preload all voices so switching works offline later
    def preload_voices(self):
        if not all_voices_cached():
            os.environ.pop('HF_HUB_OFFLINE', None)

        skipped = []
        voices_loaded = 0
        for lang_code in ('a', 'b'):
            pipeline = self.get_pipeline(lang_code)
            for voice in VOICES:
                if voice[0] != lang_code:
                    continue
                try:
                    pipeline.load_voice(voice)
                    self.available_voices.add(voice)
                    voices_loaded += 1
                except Exception as error:
                    skipped.append((voice, format_error(error)))
        lang_code = self.voice[0]
        self.pipeline = self.get_pipeline(lang_code)

        # Print skipped voices on their own lines
        if skipped:
            write_line(f"Skipped {len(skipped)} voices:")
            for voice, message in skipped:
                write_line(f"  {voice}: {message}")

        # Use offline mode after voices are cached
        if voices_loaded == len(VOICES):
            os.environ['HF_HUB_OFFLINE'] = '1'

    # Generate and play one phrase
    def speak_phrase(self, text, voice, speed):
        if self.pipeline is None or self.model is None:
            self.handle_error('Speech engine not ready.', f"Skipped '{text}'", True)
            return

        if not self.try_load_voice(voice):
            self.handle_error(f"Voice {voice} unavailable.", f"Skipped '{text}'", True)
            return

        self.cancel_flag.clear()
        self.state = 'speaking'
        write_status(format_status(self, f"Speaking: {text}"))

        total_audio_seconds = 0
        total_generate_seconds = 0

        try:
            # Generate and play each chunk
            pipeline = self.get_pipeline(voice[0])
            self.pipeline = pipeline
            generator = pipeline(text, voice=voice, speed=speed)
            chunk_start = time.perf_counter()
            for chunk_index, (graphemes, phonemes, audio) in enumerate(generator):
                if self.cancel_flag.is_set():
                    break

                generate_seconds = time.perf_counter() - chunk_start
                audio_seconds = len(audio) / 24000
                total_audio_seconds += audio_seconds
                total_generate_seconds += generate_seconds

                # Write wav file
                wav_name = str(self.audio_counter).zfill(3) + '.wav'
                self.audio_counter += 1
                wav_path = os.path.join(AUDIO_DIR, wav_name)
                soundfile.write(wav_path, audio, 24000)

                # Play wav file
                if self.cancel_flag.is_set():
                    break
                self.play_wav(wav_path)
                chunk_start = time.perf_counter()
        except Exception as error:
            self.handle_error(error, f"Failed while speaking '{text}'", True)
            return

        self.stop_player()
        self.state = 'idle'
        if not self.cancel_flag.is_set() and total_generate_seconds > 0:
            self.last_realtime_speed = total_audio_seconds / total_generate_seconds
            write_status(format_status(self, "Done."))

    # Play wav file and allow cancellation
    def play_wav(self, wav_path):
        self.stop_player()
        self.player_process = subprocess.Popen(play_wav_command(wav_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while True:
            if self.cancel_flag.is_set():
                self.stop_player()
                return
            if self.player_process.poll() is not None:
                exit_code = self.player_process.returncode
                self.player_process = None
                if exit_code != 0:
                    raise RuntimeError(f'{PLAYER} failed with exit code {exit_code}')
                return
            time.sleep(0.05)

    # Stop current audio playback
    def stop_player(self):
        if self.player_process and self.player_process.poll() is None:
            self.player_process.terminate()
            try:
                self.player_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.player_process.kill()
        self.player_process = None

# Return path to a cached voice file when present
def voice_cache_path(voice):
    if not os.path.isdir(MODEL_CACHE_DIR):
        return None
    for snapshot_name in os.listdir(MODEL_CACHE_DIR):
        voice_path = os.path.join(MODEL_CACHE_DIR, snapshot_name, 'voices', voice + '.pt')
        if os.path.isfile(voice_path):
            return voice_path
    return None

# Return true when every voice file is cached locally
def all_voices_cached():
    for voice in VOICES:
        if voice_cache_path(voice) is None:
            return False
    return True


# Return true when public internet responds to ping
def network_available():
    result = subprocess.run(['ping', '-c', '1', '-W', '2', '1.1.1.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

# Return true when downloads are not possible
def is_offline():
    if os.environ.get('HF_HUB_OFFLINE') == '1':
        return True
    return not network_available()

# Use local cache only when all voices are already downloaded
def enable_offline_if_cached():
    if all_voices_cached():
        os.environ['HF_HUB_OFFLINE'] = '1'

# Quit early when offline without cached voices
def check_offline_cache():
    if is_offline() and not all_voices_cached():
        print('Offline and voices not cached. Run once online to download, or run ./tools-offline.sh --fix.')
        sys.exit(1)

# Main
if __name__ == '__main__':
    main()
