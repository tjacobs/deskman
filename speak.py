#!.venv/bin/python

# Imports
import glob
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import warnings

# Config voice
REPO_ID = 'hexgrad/Kokoro-82M'
DEFAULT_VOICE = 'bm_fable'
SPEECH_SPEED = 1.2
TEXT = '''
Hi there!
I'm a robot, and I'm here to help you.
'''

# Config dirs and env
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
MODEL_CACHE_DIR = os.path.join(CACHE_DIR, 'models--hexgrad--Kokoro-82M', 'snapshots')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')
os.environ['HF_HUB_CACHE'] = CACHE_DIR
os.environ['HF_HUB_VERBOSITY'] = 'error'

# Config timeouts
LOAD_TIMEOUT_SECONDS = 60

# Config stats and device
STARTUP_START = time.perf_counter()
KOKORO_SECONDS = 0
DEVICE = 'cpu'
FORCE_CPU = False

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
    check_ready()

    # Run
    run_start = time.perf_counter()
    try:
        # Pick default voice
        voice = DEFAULT_VOICE
        print("Voice: " + voice)

        # Generate audio and play it
        generate_and_play(voice, TEXT)

        # Print total time
        log_timing("Run total", run_start)
        log_timing("Script total", STARTUP_START)

    # Done
    finally:
        if saved_cpu_mode:
            set_cpu_mode(saved_cpu_mode)

# Parse command line arguments
def parse_args():
    force_cpu = False
    for argument in sys.argv[1:]:
        if argument == '--cpu':
            force_cpu = True
        else:
            print(f"Unknown argument: {argument}")
            sys.exit(1)
    return force_cpu

# Import kokoro and configure runtime
def init():
    global FORCE_CPU, DEVICE, KOKORO_SECONDS

    # Parse args and configure device flags
    FORCE_CPU = parse_args()
    if FORCE_CPU:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''

    # Enable offline mode when cached, quit when offline without cache
    enable_offline_if_cached(DEFAULT_VOICE)

    # Limit torch thread pools before kokoro imports torch
    os.environ['OMP_NUM_THREADS'] = '4'
    os.environ['MKL_NUM_THREADS'] = '4'
    os.environ['OPENBLAS_NUM_THREADS'] = '4'

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
    print_import_timing()

# Generate audio for the text and play each chunk
def generate_and_play(voice, text):
    # Load the model and pipeline
    pipeline_start = time.perf_counter()
    model, pipeline = load_model_and_pipeline(voice)
    log_timing("Load pipeline", pipeline_start)

    # Generate, write, and play each chunk
    print_chunk_timing_header()
    os.makedirs(AUDIO_DIR, exist_ok=True)
    audio_counter = 1
    chunk_start = time.perf_counter()
    generator = pipeline(text, voice=voice, speed=SPEECH_SPEED)
    for index, (graphemes, phonemes, audio) in enumerate(generator):
        # Time chunk generation
        generate_seconds = time.perf_counter() - chunk_start
        audio_seconds = len(audio) / 24000

        # Write wav file
        wav_name = str(audio_counter).zfill(3) + '.wav'
        audio_counter += 1
        wav_path = os.path.join(AUDIO_DIR, wav_name)
        soundfile.write(wav_path, audio, 24000)

        # Play wav file
        play_start = time.perf_counter()
        play_result = subprocess.run(play_wav_command(wav_path), capture_output=True)
        if play_result.returncode != 0:
            exit_error(f'Audio playback failed, {audio_player()} returned exit code {play_result.returncode}.')
        play_seconds = time.perf_counter() - play_start
        log_chunk_timing(index, generate_seconds, play_seconds, audio_seconds)

        # Start timer for next chunk generation
        chunk_start = time.perf_counter()

# Load model and pipeline with a timeout
def load_model_and_pipeline(voice):
    load_result = {'model': None, 'pipeline': None, 'error': None}

    # Load model in a background thread so load can time out
    def load_work():
        try:
            model = kokoro.KModel(repo_id=REPO_ID, disable_complex=True).to(DEVICE).eval()
            pipeline = kokoro.KPipeline(lang_code=voice[0], repo_id=REPO_ID, model=model)
            pipeline.load_voice(voice)
            load_result['model'] = model
            load_result['pipeline'] = pipeline
        except Exception as error:
            load_result['error'] = error

    load_thread = threading.Thread(target=load_work, daemon=True)
    load_thread.start()
    load_thread.join(LOAD_TIMEOUT_SECONDS)
    if load_thread.is_alive():
        exit_error(f'Load timed out after {LOAD_TIMEOUT_SECONDS} seconds.')
    if load_result['error'] is not None:
        exit_error(f'Model load failed: {format_load_error(load_result["error"])}')
    return load_result['model'], load_result['pipeline']

# Print kokoro import timing
def print_import_timing():
    log_elapsed("Import kokoro", KOKORO_SECONDS)

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

# Quit when audio playback is unavailable
def check_ready():
    player_ok, player_error = check_audio_player()
    if not player_ok:
        exit_error(f'Audio playback unavailable: {player_error}')

# Print chunk timing column headers
def print_chunk_timing_header():
    print(f"{'Wav':>5}  {'Generate':>8}  {'Play':>8}  {'Speed':>6}")

# Print one chunk row with aligned generate and play times
def log_chunk_timing(index, generate_seconds, play_seconds, audio_seconds):
    speed = audio_seconds / generate_seconds if generate_seconds > 0 else 0
    print(f"{index:>5}  {format_seconds(generate_seconds):>8}  {format_seconds(play_seconds):>8}  {format_speed(speed):>6}")

# Print elapsed seconds for a timed step
def log_timing(label, start_time):
    elapsed = time.perf_counter() - start_time
    print(f"{label}: {format_seconds(elapsed)}")

# Print elapsed seconds for a stored duration
def log_elapsed(label, seconds):
    print(f"{label}: {format_seconds(seconds)}")

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
    if player == 'afplay':
        return [player, wav_path]
    card = find_usb_card()
    if card is None:
        return [player, wav_path]
    return [player, '-D', f'plughw:{card},0', wav_path]

# Return true when audio player is available
def check_audio_player():
    player = audio_player()
    if shutil.which(player) is None:
        return False, f'{player} not found'
    if player == 'aplay':
        result = subprocess.run(['aplay', '-l'], capture_output=True)
        if result.returncode != 0:
            return False, 'no audio device found'
        if find_usb_card() is None:
            return False, 'no USB audio device found, plug one in and run ./tools-audio.sh'
    return True, None

# Return audio player command for this platform
def audio_player():
    return 'afplay' if platform.system() == 'Darwin' else 'aplay'

# Format model load errors for terminal output
def format_load_error(error):
    text = str(error).strip()
    if 'offline mode is enabled' in text:
        return 'model not cached, run once online to download'
    if 'trying to locate the file on the Hub' in text:
        return 'model not cached, run once online to download'
    if 'Cannot reach' in text:
        return 'network unavailable, model not cached'
    if len(text) > 80:
        return text[:77] + '...'
    return text

# Format seconds for timing output
def format_seconds(seconds):
    return f"{seconds:.1f}s"

# Format realtime speed for timing output
def format_speed(speed):
    return f"{speed:.1f}x"

# Print error and exit
def exit_error(message):
    print(message)
    sys.exit(1)

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

# Return path to a cached voice file when present
def voice_cache_path(voice):
    if not os.path.isdir(MODEL_CACHE_DIR):
        return None
    for snapshot_name in os.listdir(MODEL_CACHE_DIR):
        voice_path = os.path.join(MODEL_CACHE_DIR, snapshot_name, 'voices', voice + '.pt')
        if os.path.isfile(voice_path):
            return voice_path
    return None

# Use local cache only when model and voice are already downloaded
def enable_offline_if_cached(voice):
    if voice_cache_path(voice) is not None:
        os.environ['HF_HUB_OFFLINE'] = '1'

# Return true when downloads are not possible
def is_offline():
    if os.environ.get('HF_HUB_OFFLINE') == '1':
        return True
    return not network_available()

# Return true when model and default voice are cached locally
def can_run_offline():
    if not os.path.isdir(MODEL_CACHE_DIR) or not os.listdir(MODEL_CACHE_DIR):
        return False
    return voice_cache_path(DEFAULT_VOICE) is not None

# Return true when public internet responds to ping
def network_available():
    result = subprocess.run(['ping', '-c', '1', '-W', '2', '1.1.1.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

# Quit early when offline without a cached model
def check_offline_cache():
    if is_offline() and not can_run_offline():
        print('Offline and model not cached. Run once online to download, or run ./tools-offline.sh --fix.')
        sys.exit(1)

# Main
if __name__ == '__main__':
    main()
