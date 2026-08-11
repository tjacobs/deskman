#!.venv/bin/python

# Talk.py is a voice assistant that uses a local speech to text, a local LLM, a local text to speech, and local wake word detection to answer questions and commands.

# Imports
import os
import re
import sys
import time
import queue
import shutil
import traceback
import warnings
import platform
import threading
import subprocess
import numpy as np
import collections
import urllib.error
import urllib.request

# Config voice
DEFAULT_VOICE = 'bm_fable'
SPEECH_SPEED = 1.2
VOICES = [
    'af_heart', 'af_alloy', 'af_aoede', 'af_bella', 'af_jessica', 'af_kore', 'af_nicole', 'af_nova', 'af_river', 'af_sarah', 'af_sky',
    'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael', 'am_onyx', 'am_puck', 'am_santa',
    'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily',
    'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis',
]

# Config wake word and phrases
WAKE_WORD = 'robot'
NEAR_WAKE_WORDS = ('rob', 'rub')
GREETING = 'Hi!'
ACKNOWLEDGEMENT = 'Question for me?'
GOODBYE = 'Goodbye!'
QUIT_WORDS = ('quit', 'exit')

# Config wake tone
WAKE_TONE_RATE = 24000
WAKE_TONE_NOTES = ((880.0, 0.12), (1174.7, 0.18))
WAKE_TONE_GAP_SECONDS = 0.04
WAKE_TONE_FADE_SECONDS = 0.015
WAKE_TONE_AMPLITUDE = 0.0625

# Config follow-up window
FOLLOW_UP_SECONDS = 20.0

# Config daily reminders
REMINDER_CHECK_SECONDS = 20.0

# Config replay flags
REPLAY_WAKE_FLAG = f'--replay-{WAKE_WORD}'
NO_REPLAY_WAKE_FLAG = f'--no-replay-{WAKE_WORD}'

# Config whisper model size
WHISPER_MODEL_SIZE = 'base'

# Config audio recording
SAMPLE_RATE = 16000
MAC_RECORDER = 'rec'
LINUX_RECORDER = 'arecord'

# Config speech detection
VAD_FRAME_SAMPLES = 512
VAD_BLOCK_FRAMES = 8
VAD_THRESHOLD = 0.5
PRE_ROLL_SECONDS = 0.5
SILENCE_END_SECONDS = 1.5
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
VOICE = DEFAULT_VOICE
MAC_PLAYER = 'afplay'
LINUX_PLAYER = 'aplay'

# Config test question and text warm-up
TEST_QUESTION = 'What is the time?'
WARMUP_PROMPT = 'Say hello.'

# Config dirs and env
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')
TALKS_DIR = os.path.join(SCRIPT_DIR, 'talks')
SPOKEN_WAV = 'talk.wav'
HEARD_WAV = 'heard.wav'
WAKE_WAV = 'wake.wav'
TEXT_DIR = os.path.join(SCRIPT_DIR, 'text')
TEXT_SERVER_SCRIPT = os.path.join(TEXT_DIR, 'server.sh')
TEXT_CUDA_LIBRARY = os.path.join(TEXT_DIR, 'llama.cpp', 'build', 'bin', 'libggml-cuda.so')
TEXT_UNAVAILABLE = 'The language model is not running.'
TEXT_SERVER_START_SECONDS = 180
TEXT_SERVER_POLL_SECONDS = 0.5
TEXT_SERVER_LOG = os.path.join(SCRIPT_DIR, 'text_server.log')
TEXT_SERVER_RESTART_TRIES = 3
ALLOW_MULTIPLE_INSTANCES = False

# Expected RAM use in gigabytes
TEXT_SERVER_EXPECTED_GB = 2.0
WHISPER_EXPECTED_GB = 0.4
KOKORO_EXPECTED_GB = 0.7
SPEECH_STACK_EXPECTED_GB = TEXT_SERVER_EXPECTED_GB + WHISPER_EXPECTED_GB + KOKORO_EXPECTED_GB
MEMORY_LOW_GB = 0.5

# Download flags
os.environ['HF_HUB_CACHE'] = CACHE_DIR
os.environ['HF_HUB_VERBOSITY'] = 'error'

# Import local text model helper and reminders
sys.path.insert(0, TEXT_DIR)
import ask as text_ask
import client as text_client
import reminders
text_ask.set_talk_module(sys.modules[__name__])

# State
TEST_MODE = False
REPEAT_MODE = False
REPLAY_MODE = False
REPLAY_WAKE_MODE = True
MEMORY_MODE = False
LAST_ASK_AT = 0.0
kokoro_model = None
kokoro_pipelines = {}
speak_lock = threading.Lock()
text_server_log_file = None
text_server_process = None

# Main
def main():
    # Parse args
    global TEST_MODE, REPEAT_MODE, REPLAY_MODE, REPLAY_WAKE_MODE, MEMORY_MODE, text_server_process
    TEST_MODE, REPEAT_MODE, REPLAY_MODE, REPLAY_WAKE_MODE, MEMORY_MODE = parse_args()

    # Exit if another talk.py is already running
    ensure_single_instance()

    # Exit if audio playback is unavailable
    check_ready()

    # Warn when free RAM is below what whisper, kokoro, and the text server need
    warn_if_low_memory()

    # Build the record command
    record = record_command()

    # Load speech first, then the text server
    try:
        # Load speech models
        whisper_model, vad_model, kokoro_pipeline = load_speech_models()

        # Load text server
        text_server_process = start_text_server()
        warm_text()

        # Run
        run_talk(record, whisper_model, vad_model, kokoro_pipeline)
    except SystemExit:
        # Raise
        raise
    except Exception as error:
        # Fail
        print_error('talk.py failed', error)
        sys.exit(1)
    finally:
        # End
        stop_text_server(text_server_process)
        text_server_process = None

# Parse command line arguments
def parse_args():
    test_mode = False
    repeat_mode = False
    replay_mode = False
    replay_wake_mode = True
    memory_mode = False
    for argument in sys.argv[1:]:
        if argument == '--test':
            test_mode = True
        elif argument == '--repeat':
            repeat_mode = True
        elif argument == '--replay':
            replay_mode = True
        elif argument == '--memory':
            memory_mode = True
        elif argument == REPLAY_WAKE_FLAG:
            replay_wake_mode = True
        elif argument == NO_REPLAY_WAKE_FLAG:
            replay_wake_mode = False
        elif argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f"Unknown argument: {argument}")
            print_usage()
            sys.exit(1)
    return test_mode, repeat_mode, replay_mode, replay_wake_mode, memory_mode

# Print usage help
def print_usage():
    print(f'Usage: ./talk.py [--test] [--repeat] [--replay] [--memory] [{NO_REPLAY_WAKE_FLAG}]')
    print(f'  --test             ask itself "{TEST_QUESTION}", answer it, then exit')
    print('  --repeat           say the transcribed words back after each utterance')
    print(f'  --replay           play the recording back after each utterance, saved as audio/{HEARD_WAV}')
    print('  --memory           print available memory while loading models')
    print(f'  {NO_REPLAY_WAKE_FLAG}  do not play back what was said to "{WAKE_WORD}"')
    print(f'  (no arg)           say "{WAKE_WORD}" then a command, asks the local LLM, and speaks the reply')
    print(f'                     by default plays back what was said to "{WAKE_WORD}"')

# Load whisper, vad, and kokoro
def load_speech_models():
    # Silence onnxruntime GPU discovery warning from the VAD
    import_onnxruntime_quietly()

    # Load speech models
    whisper_model = load_whisper_model()
    vad_model = load_vad_model()
    kokoro_pipeline = load_kokoro_pipeline()
    return whisper_model, vad_model, kokoro_pipeline

# Run the talk loop with models already loaded
def run_talk(record, whisper_model, vad_model, kokoro_pipeline):
    # Listen continuously, test mode does one exchange and exits
    listener = Listener(record, vad_model)
    try:
        run_talk_loop(whisper_model, kokoro_pipeline, listener)
    finally:
        listener.stop()

# Listen for the wake word, then a command, then reply
def run_talk_loop(whisper_model, kokoro_pipeline, listener):
    global LAST_ASK_AT

    # Seed dinner and bedtime reminders from memory, then check them in the background
    reminders.seed_reminders_from_memory()
    start_reminder_checker(listener, kokoro_pipeline)

    # Greet, then keep the conversation open so the first line needs no wake word
    speak_muted(listener, kokoro_pipeline, GREETING)
    LAST_ASK_AT = time.time()
    print(f'Follow-up open {FOLLOW_UP_SECONDS:g}s', flush=True)
    print_talk_help()
    try:
        while True:
            # Ask itself the test question, mic stays on so it hears itself
            if TEST_MODE:
                speak(kokoro_pipeline, TEST_QUESTION)
                command = hear_command(whisper_model, kokoro_pipeline, listener, TEST_QUESTION)

            # Wait for the wake word, then take the rest of what was said
            else:
                command = hear_wake_command(whisper_model, kokoro_pipeline, listener)
            if command is None:
                break
            print(f'Command: {command}', flush=True)

            # Say goodbye and stop when asked to quit
            if wants_to_quit(command):
                print(f'Reply: {GOODBYE}', flush=True)
                text_ask.last_tool_log.clear()
                log_talk(command, GOODBYE)
                speak_muted(listener, kokoro_pipeline, GOODBYE)
                print('Done.')
                break

            # Reply, mic muted so it does not hear itself
            reply = make_reply(command)
            if reply != TEXT_UNAVAILABLE:
                print(f'Reply: {reply}', flush=True)
            log_talk(command, reply)
            speak_muted(listener, kokoro_pipeline, reply)

            # Keep the conversation open so the next line needs no wake word
            LAST_ASK_AT = time.time()
            print(f'Follow-up open {FOLLOW_UP_SECONDS:g}s', flush=True)
            if TEST_MODE:
                print('Test done.')
                break
    except KeyboardInterrupt:
        print('\nDone.')
    except Exception as error:
        print_error('talk loop failed', error)
        raise

# Load whisper model on gpu when available
def load_whisper_model():
    print('Loading whisper...', flush=True)
    warn_if_low_memory_for('whisper', WHISPER_EXPECTED_GB)
    print_memory('before whisper')
    load_start = time.perf_counter()
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
        device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
        compute_type = 'float16' if device == 'cuda' else 'float32'
        model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)
    except Exception as error:
        print_error('whisper load failed', error)
        raise
    print(f'Loaded on {device_label(device)} in {time.perf_counter() - load_start:.1f} sec', flush=True)
    print_memory('after whisper')
    return model

# Load the silero speech detector bundled with faster-whisper
def load_vad_model():
    try:
        from faster_whisper.vad import get_vad_model
        model = get_vad_model()
    except Exception as error:
        print_error('vad load failed', error)
        raise
    return model

# Load kokoro speech pipeline on gpu when available
def load_kokoro_pipeline():
    global kokoro, torch, soundfile, kokoro_model

    # Suppress torch warnings before kokoro imports torch
    print('Loading kokoro...', flush=True)
    warn_if_low_memory_for('kokoro', KOKORO_EXPECTED_GB)
    load_start = time.perf_counter()
    os.environ['OMP_NUM_THREADS'] = '4'
    os.environ['MKL_NUM_THREADS'] = '4'
    os.environ['OPENBLAS_NUM_THREADS'] = '4'
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.nn.modules.rnn')
    warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')
    warnings.filterwarnings('ignore', category=UserWarning, module='torch.cuda')

    # Import kokoro and pick device
    try:
        # Import
        import kokoro
        import torch
        import soundfile
        torch.set_num_threads(4)
        device = 'cuda' if torch.cuda.is_available() else 'cpu'

        # Load model and the default voice
        kokoro_model = kokoro.KModel(repo_id=REPO_ID, disable_complex=True).to(device).eval()
        pipeline = get_kokoro_pipeline(VOICE[0])
        pipeline.load_voice(VOICE)
    except Exception as error:
        print_error('kokoro load failed', error)
        raise
    print(f'Loaded on {device_label(device)} in {time.perf_counter() - load_start:.1f} sec, voice {VOICE}', flush=True)
    print_memory('after kokoro')
    return pipeline

# Return a kokoro pipeline for one language code
def get_kokoro_pipeline(lang_code):
    if lang_code in kokoro_pipelines:
        return kokoro_pipelines[lang_code]
    pipeline = kokoro.KPipeline(lang_code=lang_code, repo_id=REPO_ID, model=kokoro_model)
    kokoro_pipelines[lang_code] = pipeline
    return pipeline

# Change the speaking voice used by talk
def set_voice(voice_name):
    global VOICE
    voice = normalize_voice_name(voice_name)
    if voice not in VOICES:
        names = ', '.join(voice_short_name(item) for item in VOICES)
        return f'Unknown voice: {voice_name}. Available: {names}.'
    if kokoro_model is None:
        return 'Speech is not ready yet.'

    # Load the voice on a pipeline for its language
    try:
        pipeline = get_kokoro_pipeline(voice[0])
        pipeline.load_voice(voice)
    except Exception as error:
        return f'Voice {voice} unavailable: {error}'
    VOICE = voice
    print(f'Voice set to {VOICE}', flush=True)
    return f'Voice set to {voice_short_name(voice)}.'

# List available speaking voices without language prefixes
def list_voices(count=None):
    names = [voice_short_name(voice) for voice in VOICES]
    current = voice_short_name(VOICE)
    if count is not None:
        count = max(1, min(len(names), int(count)))
        names = names[:count]
        return f'Current voice {current}. {count} voices: {", ".join(names)}.'
    return f'Current voice {current}. All voices: {", ".join(names)}.'

# Return the voice name without af am bf bm prefix
def voice_short_name(voice):
    parts = voice.split('_', 1)
    return parts[1] if len(parts) == 2 else voice

# Normalize a spoken or typed voice name to a kokoro id
def normalize_voice_name(voice_name):
    text = str(voice_name or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    if text in VOICES:
        return text

    # Drop a leading language gender prefix when present
    for prefix in ('af_', 'am_', 'bf_', 'bm_'):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    # Match by the name after the language prefix
    matches = [voice for voice in VOICES if voice.split('_', 1)[-1] == text]
    if len(matches) == 1:
        return matches[0]
    return text

# Print how to talk, test mode skips the wake word
def print_talk_help():
    if TEST_MODE:
        print(f'Test mode, asking itself "{TEST_QUESTION}" and answering.', flush=True)
    else:
        print(f'After Hi, talk for {FOLLOW_UP_SECONDS:g}s without "{WAKE_WORD}". Then say "{WAKE_WORD}" to talk again. "{WAKE_WORD} {QUIT_WORDS[0]}" or CTRL-C to stop.', flush=True)

# Wait for the wake word, or a follow-up while the conversation is still open
def hear_wake_command(whisper_model, kokoro_pipeline, listener):
    while True:
        # Listen for the next utterance, using follow-up replay while the window is open
        text = hear_utterance(whisper_model, kokoro_pipeline, listener, conversation_open())
        if text is None:
            return None

        # While the follow-up window is still open, treat any speech as the command
        if conversation_open():
            if not text:
                continue
            if has_wake_word(text):
                play_wake_tone(listener)
                command = text_after_wake(text)
                return command if command else text
            return text

        # Otherwise wait until the wake word turns up
        if not has_wake_word(text):
            continue

        # Tone on wake, then use the rest of the utterance or listen for more
        play_wake_tone(listener)
        command = text_after_wake(text)
        if command:
            return command
        return hear_command(whisper_model, kokoro_pipeline, listener, '')

# Return true when a recent ask still allows wake-free follow-ups
def conversation_open():
    return LAST_ASK_AT > 0.0 and (time.time() - LAST_ASK_AT) < FOLLOW_UP_SECONDS

# Transcribe the next utterance, falling back when nothing was heard
def hear_command(whisper_model, kokoro_pipeline, listener, fallback):
    command = hear_utterance(whisper_model, kokoro_pipeline, listener, True)
    if command is None:
        return None
    if not command and fallback:
        print(f'Heard nothing, asking: {fallback}', flush=True)
        return fallback
    return command

# Wait for one utterance and return what was said
def hear_utterance(whisper_model, kokoro_pipeline, listener, after_wake):
    # Transcribe one whole utterance
    audio = listener.next_utterance()
    if audio is None:
        return None
    text = transcribe(whisper_model, audio)
    if text:
        print(f'Heard: {text}', flush=True)

    # Show when it nearly heard its name
    near_miss = near_wake_word(text)
    if near_miss:
        print(f'Nearly "{WAKE_WORD}"', flush=True)

    # Play back the recording, then say the words back
    if near_miss or replay_wanted(text, after_wake):
        replay(listener, audio)
    if near_miss or (REPEAT_MODE and text):
        speak_muted(listener, kokoro_pipeline, text)
    return text

# Return true when the text almost says the wake word, but not quite
def near_wake_word(text):
    lowered = text.lower()
    if has_wake_word(lowered):
        return False
    return any(re.search(rf'\b{re.escape(word)}\b', lowered) for word in NEAR_WAKE_WORDS)

# Return true when the recording should be played back
def replay_wanted(text, after_wake):
    if REPLAY_MODE:
        return True
    return REPLAY_WAKE_MODE and (after_wake or has_wake_word(text))

# Compile the wake word pattern once
WAKE_WORD_PATTERN = re.compile(rf'\b{re.escape(WAKE_WORD)}\b', re.IGNORECASE)

# Return true when the wake word appears as its own word
def has_wake_word(text):
    return bool(WAKE_WORD_PATTERN.search(text))

# Return what was said after the wake word
def text_after_wake(text):
    match = WAKE_WORD_PATTERN.search(text)
    if not match:
        return ''
    return text[match.end():].strip(' ,.!?')

# Transcribe audio samples to text
def transcribe(whisper_model, audio):
    segments, info = whisper_model.transcribe(audio, language='en', vad_filter=True)
    return ' '.join(segment.text.strip() for segment in segments).strip()

# Return true when the command asks to stop
def wants_to_quit(command):
    text = command.lower()
    return any(word in text for word in QUIT_WORDS)

# Ask the local text model for a spoken reply
def make_reply(command):
    # No speech heard
    if not command.strip():
        text_ask.last_tool_log.clear()
        return "I didn't catch that."

    # Restart the text server a few times when it died, often from OOM
    if not ensure_text_server_alive():
        text_ask.last_tool_log.clear()
        print(f'Reply: {TEXT_UNAVAILABLE}', flush=True)
        return TEXT_UNAVAILABLE

    # Ask the local LLM, fall back when the server is down
    try:
        # Ask
        return text_ask.ask_model(command)
    except urllib.error.URLError as error:
        # Error?
        text_ask.last_tool_log.clear()

        # Start server again
        if ensure_text_server_alive():
            try:
                # Ask
                return text_ask.ask_model(command)
            except Exception as retry_error:
                # Fail
                print_error('ask retry failed', retry_error)

        # Fail
        print(f'Reply: {TEXT_UNAVAILABLE}', flush=True)
        print(f'Error: {format_llm_error(error)}', flush=True)
        print_memory('ask failed')
        return TEXT_UNAVAILABLE
    except Exception as error:
        # Fail
        text_ask.last_tool_log.clear()
        print(f'Reply: {TEXT_UNAVAILABLE}', flush=True)
        print_error('ask failed', error)
        print_memory('ask failed')
        return TEXT_UNAVAILABLE

# Return a short reason for an LLM connection failure
def format_llm_error(error):
    reason = getattr(error, 'reason', None)
    if reason is None or reason == '':
        return str(error)
    return str(reason)

# Print an error label, message, and traceback
def print_error(label, error):
    print(f'Error: {label}: {error}', flush=True)
    traceback.print_exc()

# Print available system memory in GB when --memory or free RAM is critically low
def print_memory(label):
    available_gb = available_memory_gb()
    if not should_print_memory(available_gb, MEMORY_LOW_GB):
        return
    print(f'Memory: {format_gigabytes(available_gb)} available {label}', flush=True)

# Return true when memory lines should print
def should_print_memory(available_gb, expected_gb):
    if MEMORY_MODE:
        return True
    return memory_is_low(available_gb, expected_gb)

# Return true when available RAM is below the expected amount
def memory_is_low(available_gb, expected_gb):
    return available_gb >= 0 and available_gb < expected_gb

# Format gigabytes as 1.1 GB
def format_gigabytes(gigabytes):
    return f'{gigabytes:.1f} GB'

# Return MemAvailable from /proc/meminfo in GB, or -1 when unknown
def available_memory_gb():
    try:
        with open('/proc/meminfo') as meminfo_file:
            for line in meminfo_file:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / (1024 * 1024)
    except OSError:
        pass
    return -1.0

# Append one command, tool lines, and reply to today's talk log
def log_talk(command, reply):
    os.makedirs(TALKS_DIR, exist_ok=True)
    path = os.path.join(TALKS_DIR, time.strftime('%Y-%m-%d') + '.txt')
    stamp = talk_log_time()
    with open(path, 'a', encoding='utf-8') as talk_file:
        talk_file.write(f'{stamp} {command}\n')
        for line in text_ask.last_tool_log:
            talk_file.write(f'{stamp} {line}\n')
        talk_file.write(f'{stamp} Reply: {reply}\n')
        talk_file.write('\n')

# Format log time like 7:49pm
def talk_log_time():
    now = time.localtime()
    hour = now.tm_hour % 12 or 12
    return f'{hour}:{now.tm_min:02d}{time.strftime("%p", now).lower()}'

# Speak with the mic muted so it does not hear itself
def speak_muted(listener, kokoro_pipeline, text):
    with speak_lock:
        listener.mute()
        speak(kokoro_pipeline, text)
        listener.unmute()

# Generate speech and play it on the usb speaker
def speak(kokoro_pipeline, text):
    os.makedirs(AUDIO_DIR, exist_ok=True)
    pipeline = get_kokoro_pipeline(VOICE[0])
    generator = pipeline(text, voice=VOICE, speed=SPEECH_SPEED)
    for index, (graphemes, phonemes, audio) in enumerate(generator):
        wav_path = os.path.join(AUDIO_DIR, SPOKEN_WAV)
        soundfile.write(wav_path, audio, 24000)
        play_wav(wav_path)

# Start a background thread that speaks due daily reminders
def start_reminder_checker(listener, kokoro_pipeline):
    thread = threading.Thread(target=reminder_loop, args=(listener, kokoro_pipeline), daemon=True)
    thread.start()

# Poll for due reminders and speak each one once per day
def reminder_loop(listener, kokoro_pipeline):
    while True:
        try:
            fire_due_reminders(listener, kokoro_pipeline)
        except Exception as error:
            print(f'Reminder check failed: {error}', flush=True)
        time.sleep(REMINDER_CHECK_SECONDS)

# Speak any reminders due in the current minute
def fire_due_reminders(listener, kokoro_pipeline):
    due = reminders.pop_due_reminders()
    for reminder in due:
        message = reminder.get('message') or "Reminder"
        print(f'Reminder: {message}', flush=True)
        speak_muted(listener, kokoro_pipeline, message)

# Play back the recording, mic muted so it does not hear it
def replay(listener, audio):
    listener.mute()
    os.makedirs(AUDIO_DIR, exist_ok=True)
    wav_path = os.path.join(AUDIO_DIR, HEARD_WAV)
    soundfile.write(wav_path, audio, SAMPLE_RATE)
    play_wav(wav_path)
    listener.unmute()

# Play the wake acknowledgment tone, mic muted so it does not hear it
def play_wake_tone(listener):
    listener.mute()
    play_wav(ensure_wake_tone())
    listener.unmute()

# Build audio/wake.wav once, a soft two-note blip
def ensure_wake_tone():
    import soundfile
    wav_path = os.path.join(AUDIO_DIR, WAKE_WAV)
    if os.path.isfile(wav_path):
        return wav_path

    # Build each note with a short fade, then a small gap
    os.makedirs(AUDIO_DIR, exist_ok=True)
    parts = []
    fade = int(WAKE_TONE_RATE * WAKE_TONE_FADE_SECONDS)
    gap = np.zeros(int(WAKE_TONE_RATE * WAKE_TONE_GAP_SECONDS), dtype=np.float32)
    for frequency, duration in WAKE_TONE_NOTES:
        samples = int(WAKE_TONE_RATE * duration)
        times = np.arange(samples, dtype=np.float32) / WAKE_TONE_RATE
        wave = (WAKE_TONE_AMPLITUDE * np.sin(2.0 * np.pi * frequency * times)).astype(np.float32)
        if fade > 0 and samples > 2 * fade:
            wave[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
            wave[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        parts.append(wave)
        parts.append(gap)
    soundfile.write(wav_path, np.concatenate(parts), WAKE_TONE_RATE)
    return wav_path

# Play one wav file on the speaker
def play_wav(wav_path):
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

# Start the local text model server when needed, return the process we started
def start_text_server(require_success=True):
    print('Loading text model server...', flush=True)
    warn_if_low_memory_for('text server', TEXT_SERVER_EXPECTED_GB)
    load_start = time.perf_counter()

    # Reuse a server that is already healthy
    if text_server_healthy():
        print_text_server_already_running()
        print_memory('after text server')
        return None

    # Quit when the install is incomplete
    if not os.access(TEXT_SERVER_SCRIPT, os.X_OK):
        print(f'Text server missing. Run ./install.sh --talk first.')
        if require_success:
            sys.exit(1)
        return None

    # Start server.sh and keep its log so startup failures are visible
    process = spawn_text_server()

    # Wait until the health endpoint answers
    deadline = time.time() + TEXT_SERVER_START_SECONDS
    while time.time() < deadline:
        if process.poll() is not None:
            text_server_log_file.flush()
            print('Text server failed to start. Run ./text/server.sh to see the error.', flush=True)
            print_log_tail(TEXT_SERVER_LOG)
            if require_success:
                sys.exit(1)
            return None
        if text_server_healthy():
            print_text_server_started(load_start)
            print_memory('after text server')
            return process
        time.sleep(TEXT_SERVER_POLL_SECONDS)

    # Timed out waiting for the model to load
    stop_text_server(process)
    text_server_log_file.flush()
    print('Text server did not become ready in time.', flush=True)
    print_log_tail(TEXT_SERVER_LOG)
    if require_success:
        sys.exit(1)
    return None

# Spawn server.sh and replace the log file handle
def spawn_text_server():
    global text_server_log_file
    if text_server_log_file is not None:
        text_server_log_file.close()
    text_server_log_file = open(TEXT_SERVER_LOG, 'w')
    return subprocess.Popen([TEXT_SERVER_SCRIPT], cwd=TEXT_DIR, stdout=text_server_log_file, stderr=subprocess.STDOUT)

# Restart the text server when it died, return true when healthy again
def ensure_text_server_alive():
    global text_server_process

    # Already up
    if text_server_healthy():
        return True

    # Often out of memory on 8GB machines
    print('text server is not responding, it may have been killed by an out of memory error.', flush=True)
    print_memory('text server down')

    # Retry a few cold starts
    for attempt in range(1, TEXT_SERVER_RESTART_TRIES + 1):
        warn_if_low_memory_for('text server restart', TEXT_SERVER_EXPECTED_GB)
        print(f'Restarting text server, try {attempt}/{TEXT_SERVER_RESTART_TRIES}...', flush=True)
        stop_text_server(text_server_process)
        text_server_process = None
        process = start_text_server(require_success=False)
        if text_server_healthy():
            text_server_process = process
            warm_text()
            return True
        stop_text_server(process)

    print(f'Error: text server still down after {TEXT_SERVER_RESTART_TRIES} restarts.', flush=True)
    print_memory('text server restart failed')
    return False

# Print the last lines of a log file
def print_log_tail(path, line_count=40):
    try:
        with open(path) as log_file:
            lines = log_file.read().splitlines()
    except OSError as error:
        print(f'Error: could not read {path}: {error}', flush=True)
        return
    if not lines:
        print(f'Error: {path} is empty.', flush=True)
        return
    print(f'----- {path} -----', flush=True)
    for line in lines[-line_count:]:
        print(line, flush=True)
    print('----- end -----', flush=True)

# Print when talk started the text server
def print_text_server_started(load_start):
    print(f'Text model server started in {time.perf_counter() - load_start:.1f} sec on {text_server_device()}. Model: {text_ask.resolve_model_name()}', flush=True)

# Print when a text server was already healthy
def print_text_server_already_running():
    print(f'Text model server already running. Model: {text_ask.resolve_model_name()}.', flush=True)

# Return GPU when llama.cpp was built with the CUDA library, else CPU
def text_server_device():
    if os.path.exists(TEXT_CUDA_LIBRARY):
        return 'GPU'
    return 'CPU'

# Return a user-facing device name, GPU instead of CUDA
def device_label(device):
    if device == 'cuda':
        return 'GPU'
    return device.upper()

# Return true when the local text model health endpoint answers
def text_server_healthy():
    try:
        request = urllib.request.Request(text_client.HEALTH_URL, headers={'Authorization': f'Bearer {text_client.API_KEY}'})
        urllib.request.urlopen(request, timeout=2)
        return True
    except urllib.error.URLError:
        return False

# Stop a text model server that talk.py started
def stop_text_server(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

# Run one hello ask so the system prompt is prefilled into the text model cache
def warm_text():
    print('Starting text model inference...', flush=True)
    load_start = time.perf_counter()
    try:
        text_ask.ask_model(WARMUP_PROMPT)
    except urllib.error.URLError as error:
        print(f'Start failed: {TEXT_UNAVAILABLE}', flush=True)
        print(f'Error: {format_llm_error(error)}', flush=True)
        return
    except Exception as error:
        print_error('warm text failed', error)
        return

    # Drop the warm-up turn so the first spoken ask starts a fresh conversation
    text_ask.conversation_history.clear()
    text_ask.last_tool_log.clear()
    print(f'Started in {time.perf_counter() - load_start:.1f} sec', flush=True)
    print_memory('after warm text')

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
    return [audio_player(), wav_path]

# Return audio player command for this platform
def audio_player():
    return MAC_PLAYER if platform.system() == 'Darwin' else LINUX_PLAYER

# Warn when free RAM is below the expected cost of the three heavy loads
def warn_if_low_memory():
    available_gb = available_memory_gb()
    expected_gb = SPEECH_STACK_EXPECTED_GB
    low = memory_is_low(available_gb, expected_gb)
    if should_print_memory(available_gb, expected_gb):
        print(f'Memory: expect about {format_gigabytes(expected_gb)} for text server ({format_gigabytes(TEXT_SERVER_EXPECTED_GB)}), whisper ({format_gigabytes(WHISPER_EXPECTED_GB)}), and kokoro ({format_gigabytes(KOKORO_EXPECTED_GB)}), {format_gigabytes(available_gb)} available', flush=True)
    if low:
        print(f'Warning: only {format_gigabytes(available_gb)} available, need about {format_gigabytes(expected_gb)}. The text model may be killed by an out of memory error.', flush=True)

# Warn when free RAM is below one load step
def warn_if_low_memory_for(name, expected_gb):
    available_gb = available_memory_gb()
    if memory_is_low(available_gb, expected_gb):
        print(f'Warning: only {format_gigabytes(available_gb)} available before {name}, expect about {format_gigabytes(expected_gb)}.', flush=True)

# Quit when another talk.py process is already alive
def ensure_single_instance():
    # Temp: allow a second talk.py so memory pressure can be reproduced
    if ALLOW_MULTIPLE_INSTANCES:
        other_pid = find_other_talk_pid()
        if other_pid is not None:
            print(f'Warning: talk.py already running, pid {other_pid}, continuing anyway.', flush=True)
        return

    other_pid = find_other_talk_pid()
    if other_pid is not None:
        print(f'talk.py is already running, pid {other_pid}.')
        print('Stop it with: sudo service robot stop; sudo service talk stop')
        sys.exit(1)

# Return the pid of another talk.py process, or None
def find_other_talk_pid():
    my_pid = os.getpid()
    result = subprocess.run(['ps', 'ax', '-o', 'pid=,command='], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        pid = int(parts[0])
        if pid == my_pid:
            continue
        command = parts[1]
        if 'talk.py' in command and 'python' in command:
            return pid
    return None

# Main
if __name__ == '__main__':
    main()
