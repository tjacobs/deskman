# Shared helpers for speak.py, say.py, talk.py, and speech_listen.py.

# Imports
import os
import sys
import time
import glob
import shutil
import platform
import subprocess
import warnings

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')
MODEL_CACHE_DIR = os.path.join(CACHE_DIR, 'models--hexgrad--Kokoro-82M', 'snapshots')

# Config kokoro
REPO_ID = 'hexgrad/Kokoro-82M'
DEFAULT_VOICE = 'bm_fable'
VOICES = [
    'af_heart', 'af_alloy', 'af_aoede', 'af_bella', 'af_jessica', 'af_kore', 'af_nicole', 'af_nova', 'af_river', 'af_sarah', 'af_sky',
    'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael', 'am_onyx', 'am_puck', 'am_santa',
    'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily',
    'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis',
]
TORCH_THREADS = 4

# Config audio
MAC_PLAYER = 'afplay'
LINUX_PLAYER = 'aplay'
MAC_RECORDER = 'rec'
LINUX_RECORDER = 'arecord'
SAMPLE_RATE = 16000
CARDS_PATH = '/proc/asound/cards'
VIDEO_DIR = '/sys/class/video4linux'
CPU_SCALING_DIR = '/sys/devices/system/cpu'
CPU_INDEX_MAX = 64

# Config huggingface cache
os.environ['HF_HUB_CACHE'] = CACHE_DIR
os.environ['HF_HUB_VERBOSITY'] = 'error'

# Return audio player command for this platform
def audio_player():
    return MAC_PLAYER if platform.system() == 'Darwin' else LINUX_PLAYER

# Build playback command for one wav file
def play_wav_command(wav_path):
    # Play through the default device, tools/audio.sh points that at the USB soundcard
    return [audio_player(), wav_path]

# Return true when audio player is available
def check_audio_player():
    player = audio_player()
    if shutil.which(player) is None:
        return False, f'{player} not found'
    if player == LINUX_PLAYER:
        result = subprocess.run([LINUX_PLAYER, '-l'], capture_output=True)
        if result.returncode != 0:
            return False, 'no audio device found'
        if find_usb_card() is None:
            return False, 'no USB audio device found, plug one in and run ./tools/audio.sh'
    return True, None

# Return card index for the playback-only USB sound device
def find_usb_card():
    if not os.path.isfile(CARDS_PATH):
        return None

    # Collect USB card indexes
    usb_cards = []
    with open(CARDS_PATH) as cards_file:
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

# Return true when a card has a capture stream
def card_has_capture(card_index):
    return card_stream_has(card_index, 'Capture:')

# Return true when a card stream file contains a label
def card_stream_has(card_index, label):
    stream_path = f'/proc/asound/card{card_index}/stream0'
    if not os.path.isfile(stream_path):
        return False
    with open(stream_path) as stream_file:
        return label in stream_file.read()

# Return card index for the USB microphone
def find_capture_card():
    # Collect USB cards that can record, skip cameras with no mic
    result = subprocess.run([LINUX_RECORDER, '-l'], capture_output=True, text=True)
    usb_cards = []
    for line in result.stdout.splitlines():
        if line.startswith('card') and 'USB' in line:
            card_index = int(line.split(':')[0].split()[1])
            if not card_is_camera(card_index):
                usb_cards.append(card_index)

    # Prefer the mic-only card, a speaker card records through a poor fallback mic
    for card_index in usb_cards:
        if not card_has_playback(card_index):
            return card_index

    # Otherwise take the first one
    if usb_cards:
        return usb_cards[0]
    return None

# Return true when this sound card is a camera, not a microphone
def card_is_camera(card_index):
    sound_link = f'/sys/class/sound/card{card_index}/device'
    if not os.path.exists(sound_link):
        return False
    usb_device = os.path.dirname(os.path.realpath(sound_link))
    if not os.path.isdir(VIDEO_DIR):
        return False
    for video_name in os.listdir(VIDEO_DIR):
        video_link = os.path.join(VIDEO_DIR, video_name, 'device')
        if not os.path.exists(video_link):
            continue
        if os.path.dirname(os.path.realpath(video_link)) == usb_device:
            return True
    return False

# Return true when a card has a playback stream
def card_has_playback(card_index):
    return card_stream_has(card_index, 'Playback:')

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

# Start the recorder streaming raw audio to stdout
def start_recorder(command):
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

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
    return not import_failed

# Read cpu scaling mode for the first core
def get_cpu_mode():
    scaling_file_path = f'{CPU_SCALING_DIR}/cpu0/cpufreq/scaling_governor'
    if not os.path.exists(scaling_file_path):
        return None
    with open(scaling_file_path) as scaling_file:
        return scaling_file.read().strip()

# Set cpu scaling mode for all cores, return true on success
def set_cpu_mode(mode):
    for cpu_index in range(CPU_INDEX_MAX):
        scaling_path = f'{CPU_SCALING_DIR}/cpu{cpu_index}/cpufreq/scaling_governor'
        if not os.path.exists(scaling_path):
            break
        try:
            with open(scaling_path, 'w') as scaling_file:
                scaling_file.write(mode)
        except OSError:
            return False
    return True

# Print cpu, gpu, and device info
def print_system_info(perf_set, device, force_cpu, torch_module):
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
    if device == 'cuda':
        properties = torch_module.cuda.get_device_properties(0)
        frequency = read_gpu_frequency_mhz()
        clock_text = f"{frequency / 1000:.1f}GHz" if frequency else "unknown"
        memory_gigabytes = properties.total_memory / 1024 / 1024 / 1024
        print(f"GPU: {properties.name}, {memory_gigabytes:.1f}GB memory, clock {clock_text}")
    elif force_cpu:
        print("GPU: disabled")
    else:
        print("GPU: not available")
    print(f"Device: {'gpu' if device == 'cuda' else device}")

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
def pick_device(force_cpu, torch_module):
    if force_cpu:
        return 'cpu'
    if torch_module.cuda.is_available():
        return 'cuda'
    return 'cpu'

# Limit torch thread pools before kokoro imports torch
def configure_torch_threads():
    thread_count = str(TORCH_THREADS)
    os.environ['OMP_NUM_THREADS'] = thread_count
    os.environ['MKL_NUM_THREADS'] = thread_count
    os.environ['OPENBLAS_NUM_THREADS'] = thread_count

# Suppress torch warnings during kokoro import
def suppress_torch_warnings():
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.nn.modules.rnn')
    warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.cuda')

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

# Return true when every voice file is cached locally
def all_voices_cached(voices):
    for voice in voices:
        if voice_cache_path(voice) is None:
            return False
    return True

# Use local cache only when all voices are already downloaded
def enable_offline_if_voices_cached(voices):
    if all_voices_cached(voices):
        os.environ['HF_HUB_OFFLINE'] = '1'

# Return true when downloads are not possible
def is_offline():
    if os.environ.get('HF_HUB_OFFLINE') == '1':
        return True
    return not network_available()

# Return true when public internet responds to ping
def network_available():
    result = subprocess.run(['ping', '-c', '1', '-W', '2', '1.1.1.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

# Return true when model and default voice are cached locally
def can_run_offline():
    if not os.path.isdir(MODEL_CACHE_DIR) or not os.listdir(MODEL_CACHE_DIR):
        return False
    return voice_cache_path(DEFAULT_VOICE) is not None

# Format huggingface load errors for terminal output
def format_hub_error(error, not_cached_text, network_text):
    text = str(error).strip()
    if 'offline mode is enabled' in text:
        return not_cached_text
    if 'trying to locate the file on the Hub' in text:
        return not_cached_text
    if 'Cannot reach' in text:
        return network_text
    if len(text) > 80:
        return text[:77] + '...'
    return text

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

# Format model load errors for terminal output
def format_load_error(error):
    return format_hub_error(error, 'model not cached, run once online to download', 'network unavailable, model not cached')

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

# Quit early when offline without a cached model
def check_offline_cache():
    if is_offline() and not can_run_offline():
        print('Offline and model not cached. Run once online to download, or run ./tools/offline.sh --fix.')
        sys.exit(1)
