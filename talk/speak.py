#!.venv/bin/python

# Imports
import os
import sys
import time
import threading
import subprocess
import utils

# Config voice
SPEECH_SPEED = 1.2
TEXT = '''
Hi there!
I'm a robot, and I'm here to help you.
'''

# Config timeouts
LOAD_TIMEOUT_SECONDS = 60

# Config stats and device
STARTUP_START = time.perf_counter()
KOKORO_SECONDS = 0
DEVICE = 'cpu'
FORCE_CPU = False
SPEAK_TEXT = TEXT
PLAYBACK_AVAILABLE = True

def main():
    # Load kokoro
    init()

    # Set CPU mode to performance, restore when done
    saved_cpu_mode = utils.get_cpu_mode()
    perf_set = utils.set_cpu_mode('performance')
    utils.print_system_info(perf_set, DEVICE, FORCE_CPU, torch)

    # Warn when audio playback is unavailable, generation still runs
    check_ready()

    # Run
    run_start = time.perf_counter()
    try:
        # Pick default voice
        voice = utils.DEFAULT_VOICE
        print("Voice: " + voice)

        # Generate audio and play it
        generate_and_play(voice, SPEAK_TEXT)

        # Print total time
        utils.log_timing("Run total", run_start)
        utils.log_timing("Script total", STARTUP_START)

        # Suggest the interactive speech tool next
        print('Next: run ./say.py for more advanced speech.')

    # Done
    finally:
        if saved_cpu_mode:
            utils.set_cpu_mode(saved_cpu_mode)

# Parse command line arguments
def parse_args():
    force_cpu = False
    words = []
    for argument in sys.argv[1:]:
        if argument == '--cpu':
            force_cpu = True
            continue
        if argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        words.append(argument)
    text = " ".join(words) if words else TEXT
    return force_cpu, text

# Print usage help
def print_usage():
    print('Usage: ./speak.py [--cpu] [text...]')
    print('  --cpu     force CPU inference instead of GPU')
    print('  text      optional words to speak instead of the TEXT constant')
    print('  (no arg)  speak the TEXT constant once, with timing stats')

# Import kokoro and configure runtime
def init():
    global FORCE_CPU, DEVICE, KOKORO_SECONDS, SPEAK_TEXT

    # Parse args and configure device flags
    FORCE_CPU, SPEAK_TEXT = parse_args()
    if FORCE_CPU:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''

    # Enable offline mode when cached, quit when offline without cache
    utils.enable_offline_if_cached(utils.DEFAULT_VOICE)

    # Limit torch thread pools before kokoro imports torch
    utils.configure_torch_threads()

    # Suppress warnings
    utils.suppress_torch_warnings()

    # Import kokoro and pick device
    print("Loading...")
    kokoro_start = time.perf_counter()
    global kokoro, torch, soundfile
    import kokoro
    import torch
    import soundfile
    KOKORO_SECONDS = time.perf_counter() - kokoro_start
    DEVICE = utils.pick_device(FORCE_CPU, torch)
    torch.set_num_threads(utils.TORCH_THREADS)
    print_import_timing()

# Generate audio for the text and play each chunk
def generate_and_play(voice, text):
    # Load the model and pipeline
    pipeline_start = time.perf_counter()
    model, pipeline = load_model_and_pipeline(voice)
    utils.log_timing("Load pipeline", pipeline_start)

    # Generate, write, and play each chunk
    utils.print_chunk_timing_header()
    os.makedirs(utils.AUDIO_DIR, exist_ok=True)
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
        wav_path = os.path.join(utils.AUDIO_DIR, wav_name)
        soundfile.write(wav_path, audio, 24000)

        # Play the wav when playback is available, else skip it
        play_start = time.perf_counter()
        if PLAYBACK_AVAILABLE:
            play_result = subprocess.run(utils.play_wav_command(wav_path), capture_output=True)
            if play_result.returncode != 0:
                utils.exit_error(f'Audio playback failed, {utils.audio_player()} returned exit code {play_result.returncode}.')
        play_seconds = time.perf_counter() - play_start
        utils.log_chunk_timing(index, generate_seconds, play_seconds, audio_seconds)

        # Start timer for next chunk generation
        chunk_start = time.perf_counter()

# Load model and pipeline with a timeout
def load_model_and_pipeline(voice):
    load_result = {'model': None, 'pipeline': None, 'error': None}

    # Load model in a background thread so load can time out
    def load_work():
        try:
            model = kokoro.KModel(repo_id=utils.REPO_ID, disable_complex=True).to(DEVICE).eval()
            pipeline = kokoro.KPipeline(lang_code=voice[0], repo_id=utils.REPO_ID, model=model)
            pipeline.load_voice(voice)
            load_result['model'] = model
            load_result['pipeline'] = pipeline
        except Exception as error:
            load_result['error'] = error

    load_thread = threading.Thread(target=load_work, daemon=True)
    load_thread.start()
    load_thread.join(LOAD_TIMEOUT_SECONDS)
    if load_thread.is_alive():
        utils.exit_error(f'Load timed out after {LOAD_TIMEOUT_SECONDS} seconds.')
    if load_result['error'] is not None:
        utils.exit_error(f'Model load failed: {utils.format_load_error(load_result["error"])}')
    return load_result['model'], load_result['pipeline']

# Print kokoro import timing
def print_import_timing():
    utils.log_elapsed("Import kokoro", KOKORO_SECONDS)

# Warn when audio playback is unavailable, generation still runs
def check_ready():
    global PLAYBACK_AVAILABLE
    player_ok, player_error = utils.check_audio_player()
    if not player_ok:
        PLAYBACK_AVAILABLE = False
        print(f'Audio playback unavailable: {player_error}, generating without playback.')

# Main
if __name__ == '__main__':
    main()
