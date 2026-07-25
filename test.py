#!.venv/bin/python

# Imports
import glob
import os
import platform
import shutil
import signal
import subprocess
import sys

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK = os.path.join(SCRIPT_DIR, 'speak.py')
SAY = os.path.join(SCRIPT_DIR, 'say.py')
TALK = os.path.join(SCRIPT_DIR, 'talk.py')
OFFLINE_TOOL = os.path.join(SCRIPT_DIR, 'tools-offline.sh')
CACHE_DIR = os.path.join(SCRIPT_DIR, 'cache')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')
ONLINE_TIMEOUT_SECONDS = 30
OFFLINE_TIMEOUT_SECONDS = 30
TALK_TIMEOUT_SECONDS = 120
MAC_RECORDER = 'rec'
LINUX_RECORDER = 'arecord'
STEP_NAME_WIDTH = 19
GREEN = '\033[92m'
RED = '\033[91m'
RESET = '\033[0m'

# State
INTERNET_BLOCKED = False

# Main
def main():
    # Parse args
    fresh = parse_args()
    signal.signal(signal.SIGINT, handle_interrupt)
    if fresh:
        clean_dirs()

    # Track failures across all steps
    failed = False

    # Print
    print("Testing...")

    # Require internet for first-run downloads
    if not check_online():
        print('Need internet for first-run cache download.')
        sys.exit(1)

    # Run online cuda tests when cuda is available
    failed |= not run_step('Online speak.py', lambda: run_speak([], *device()))
    failed |= not run_step('Online say.py', lambda: run_say(['--test'], *device()))

    # Run the talk test when speech to text and a microphone are available
    talk_blocker = find_talk_blocker()
    if talk_blocker:
        print_skip(f'talk.py, {talk_blocker}')
    else:
        failed |= not run_step('Online talk.py', lambda: run_talk(['--test'], 'Test done.', *talk_device()))

    # Run cpu mode tests
    failed |= not run_step('CPU speak.py', lambda: run_speak(['--cpu'], 'Device: cpu', 'GPU: disabled'))
    failed |= not run_step('CPU say.py', lambda: run_say(['--test', '--cpu'], 'Device: cpu', 'GPU: disabled'))

    # Run offline tests
    blocked = try_block_internet()
    try:
        if blocked:
            failed |= not run_step('Offline speak.py', lambda: run_speak([], timeout=OFFLINE_TIMEOUT_SECONDS))
            failed |= not run_step('Offline say.py', lambda: run_say(['--test'], timeout=OFFLINE_TIMEOUT_SECONDS))
        else:
            print_skip('Firewall offline tests, using HF_HUB_OFFLINE instead')
            failed |= not run_step('Offline speak.py', lambda: run_speak([], env=offline_env(), timeout=OFFLINE_TIMEOUT_SECONDS))
            failed |= not run_step('Offline say.py', lambda: run_say(['--test'], env=offline_env(), timeout=OFFLINE_TIMEOUT_SECONDS))
    finally:
        restore_internet_if_blocked()

    # Exit with pass or fail
    if failed:
        print_fail('One or more tests')
        sys.exit(1)
    print_pass('All tests')

# Parse command line arguments
def parse_args():
    fresh = False
    for argument in sys.argv[1:]:
        if argument == '--fresh':
            fresh = True
        elif argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f"Unknown argument: {argument}")
            print_usage()
            sys.exit(1)
    return fresh

# Print usage help
def print_usage():
    print('Usage: ./test.py [--fresh]')
    print('  --fresh   clear cache/ and audio/ before testing')
    print('  (no arg)  run the speak, say, and talk tests online, on cpu, and offline')

# Remove cache and audio dirs for a fresh run
def clean_dirs():
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    shutil.rmtree(AUDIO_DIR, ignore_errors=True)

# Run one named test step
def run_step(name, function):
    # Print
    print(f"== {name:<{STEP_NAME_WIDTH}} == ", end='', flush=True)

    # Run
    result = function()

    # Check result
    if isinstance(result, tuple):
        passed, detail = result
    else:
        passed, detail = result, None
    if passed:
        print(f'{GREEN}PASS{RESET}')
    else:
        print(f'{RED}FAIL{RESET}')
        if detail:
            print(detail, end='' if detail.endswith('\n') else '\n')
    return passed

# Return true when public internet responds to ping
def check_online():
    # Ping
    result = subprocess.run(['ping', '-c', '1', '-W', '2', '1.1.1.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0

# Return expected output for default online device
def device():
    if cuda_available():
        return ('Device: cuda',)
    return ()

# Return true when torch sees cuda
def cuda_available():
    result = subprocess.run([sys.executable, '-c', "import torch; print(torch.cuda.is_available())"], capture_output=True, text=True, cwd=SCRIPT_DIR)
    return result.stdout.strip() == 'True'

# Return why talk.py cannot run, or none when it can
def find_talk_blocker():
    if not whisper_available():
        return 'faster-whisper not installed, run ./install.sh --listen'
    if not microphone_available():
        return 'no microphone found'
    return None

# Return true when faster-whisper is installed
def whisper_available():
    result = subprocess.run([sys.executable, '-c', "import faster_whisper"], capture_output=True, cwd=SCRIPT_DIR)
    return result.returncode == 0

# Return true when a microphone is available for this platform
def microphone_available():
    # Record with sox on mac
    if platform.system() == 'Darwin':
        return shutil.which(MAC_RECORDER) is not None

    # Look for a USB capture card on linux
    if shutil.which(LINUX_RECORDER) is None:
        return False
    result = subprocess.run([LINUX_RECORDER, '-l'], capture_output=True, text=True)
    return any(line.startswith('card') and 'USB' in line for line in result.stdout.splitlines())

# Return expected output for the device talk.py loads on
def talk_device():
    if cuda_available():
        return ('Loaded on CUDA',)
    return ('Loaded on CPU',)

# Run speak.py with args and check output
def run_speak(args, *expects, env=None, timeout=ONLINE_TIMEOUT_SECONDS):
    return run_script(SPEAK, args, expects, env, timeout)

# Run say.py with args and check output
def run_say(args, *expects, env=None, timeout=ONLINE_TIMEOUT_SECONDS):
    return run_script(SAY, args, expects, env, timeout)

# Run talk.py with args and check output
def run_talk(args, *expects, env=None, timeout=TALK_TIMEOUT_SECONDS):
    return run_script(TALK, args, expects, env, timeout)

# Run a script and verify output and audio files
def run_script(script, args, expects, env, timeout):
    # Build env for subprocess
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    # Run script and capture output
    try:
        result = subprocess.run([script] + args, capture_output=True, text=True, cwd=SCRIPT_DIR, env=run_env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout} seconds."

    output = result.stdout + result.stderr
    if result.returncode != 0:
        return False, output

    # Check expected output strings
    for expected in expects:
        if expected not in output:
            return False, f"Missing in output: {expected}\n{output}"

    # Check wav files were written
    if len(glob.glob(os.path.join(AUDIO_DIR, '*.wav'))) < 1:
        return False, f"Missing wav files in {AUDIO_DIR}"
    return True, None

# Return env dict for huggingface offline mode
def offline_env():
    return {'HF_HUB_OFFLINE': '1'}

# Block internet with tools-offline.sh when sudo works
def try_block_internet():
    global INTERNET_BLOCKED
    result = subprocess.run([OFFLINE_TOOL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    INTERNET_BLOCKED = result.returncode == 0
    return INTERNET_BLOCKED

# Restore internet after tools-offline.sh
def try_restore_internet():
    subprocess.run([OFFLINE_TOOL, '--fix'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)

# Restore internet when firewall block is active
def restore_internet_if_blocked():
    global INTERNET_BLOCKED
    if not INTERNET_BLOCKED:
        return
    try_restore_internet()
    INTERNET_BLOCKED = False

# Restore internet on ctrl-c
def handle_interrupt(signum, frame):
    restore_internet_if_blocked()
    raise KeyboardInterrupt

# Print pass line
def print_pass(message):
    print(f"{GREEN}PASS{RESET}: {message}")

# Print fail line
def print_fail(message):
    print(f"{RED}FAIL{RESET}: {message}")

# Print skip line
def print_skip(message):
    print(f"SKIP: {message}")

# Main
if __name__ == '__main__':
    main()
