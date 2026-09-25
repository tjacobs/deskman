#!.venv/bin/python

# Installs the API key for OpenAI.

# Imports
import os
import sys
import json
import time
import shutil
import subprocess
import urllib.error
import urllib.request
import utils

# Where the user creates a key, and the endpoint that proves the key works
KEY_PAGE_URL = 'https://platform.openai.com/api-keys'
KEY_CHECK_URL = 'https://api.openai.com/v1/models'
KEY_CHECK_TIMEOUT_SECONDS = 20

# Keys start with this, used to spot a clipboard that holds something else
KEY_PREFIX = 'sk-'

# Browsers tried in order, the first one installed wins
BROWSERS = ('chromium', 'chromium-browser', 'firefox')

# Window size for the browser, kept inside the panel width so nothing runs off screen
BROWSER_WINDOW = '1000,460'

# Let the browser map its window before the paste box opens, so the box lands on top
BROWSER_SETTLE_SECONDS = 8

# Chromium picks X11 by default and dies without a display, labwc needs it told
WAYLAND_FLAG = '--ozone-platform=wayland'

# Title, prompt, and width of the paste box
DIALOG_TITLE = 'OpenAI key'
DIALOG_TEXT = 'Log in, create a key, copy it, then paste it here'
DIALOG_WIDTH = '600'

# Import the text model helper so the key lands where talk already looks for it
sys.path.insert(0, os.path.join(utils.SCRIPT_DIR, 'text'))
import client as text_client

# Main
def main():
    # Parse args
    run_talk, force_terminal = parse_args()

    # Say when a key is already saved, replacing it is still allowed
    report_existing_key()

    # Open the key page unless we are headless, the person needs it to create a key
    opened = open_key_page(force_terminal)

    # Ask for the key in a window when there is a screen, otherwise on the terminal
    key = ask_for_key(force_terminal or not opened)
    if not key:
        print('No key entered.', flush=True)
        sys.exit(1)

    # Reject a key OpenAI does not accept, saving a bad one just fails later
    if not check_key(key):
        sys.exit(1)

    # Save it where load_openai_env_file reads it from
    save_key(key)

    # Start talk when asked, otherwise say how to
    if run_talk:
        start_talk()
        return
    print('Run ./talk.py to use it.', flush=True)

# Parse command line arguments
def parse_args():
    run_talk = False
    force_terminal = False
    for argument in sys.argv[1:]:
        if argument == '--run':
            run_talk = True
        elif argument == '--terminal':
            force_terminal = True
        elif argument in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        else:
            print(f'Unknown argument: {argument}', flush=True)
            print_usage()
            sys.exit(1)
    return run_talk, force_terminal

# Print usage help
def print_usage():
    print('Usage: ./install_openai_key.sh [--run] [--terminal]', flush=True)
    print('  --run       start talk.py once the key is saved', flush=True)
    print('  --terminal  skip the browser and paste box, prompt on the terminal', flush=True)
    print('  (no arg)    open the key page, paste the key in a window, save it', flush=True)

# Print whether a key is already in place
def report_existing_key():
    text_client.load_openai_env_file()
    if text_client.cloud_api_key():
        print('A key is already saved, entering a new one replaces it.', flush=True)

# Open the OpenAI key page in a browser on the screen
def open_key_page(force_terminal):
    # Skip the browser when asked, or when there is no display to put it on
    has_display = os.environ.get('WAYLAND_DISPLAY') or os.environ.get('DISPLAY')
    if force_terminal or not has_display:
        return False

    # Find a browser, the image may not carry all of them
    browser = find_browser()
    if not browser:
        print('No browser found, paste the key on the terminal instead.', flush=True)
        return False

    # Launch it detached, it stays open while the key is pasted
    print(f'Opening {KEY_PAGE_URL}', flush=True)
    command = browser_command(browser)
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    # Wait for its window, a browser that maps later would cover the paste box
    time.sleep(BROWSER_SETTLE_SECONDS)
    return True

# First installed browser, empty when none are
def find_browser():
    for browser in BROWSERS:
        path = shutil.which(browser)
        if path:
            return path
    return ''

# Build the browser command, Chromium gets a plain window with no tabs or restore prompt
def browser_command(browser):
    if 'chromium' not in browser:
        return [browser, KEY_PAGE_URL]
    command = [browser, f'--app={KEY_PAGE_URL}', f'--window-size={BROWSER_WINDOW}', '--no-first-run', '--disable-session-crashed-bubble']

    # Name the backend on Wayland, Chromium otherwise looks for an X server that is not there
    if os.environ.get('WAYLAND_DISPLAY'):
        command.insert(1, WAYLAND_FLAG)
    return command

# Get the key from a paste box, or from the terminal when there is no screen
def ask_for_key(use_terminal):
    if use_terminal:
        return ask_on_terminal()
    return ask_in_window()

# Prompt for the key on the terminal
def ask_on_terminal():
    print(f'Create a key at {KEY_PAGE_URL}', flush=True)
    try:
        return input('Paste key: ').strip()
    except (EOFError, KeyboardInterrupt):
        return ''

# Show a paste box, prefilled from the clipboard so a copied key needs one tap
def ask_in_window():
    # Prefill only when the clipboard already holds something key shaped
    prefill = clipboard_key()

    # Fall back to the terminal when zenity is missing
    zenity = shutil.which('zenity')
    if not zenity:
        return ask_on_terminal()

    # Say how to get back to the box, clicking the browser raises it over the box
    print(f'Paste the key in the {DIALOG_TITLE} box, tap it in the taskbar if the browser covers it.', flush=True)

    # Ask, a cancel returns nothing
    command = [zenity, '--entry', '--title', DIALOG_TITLE, '--text', DIALOG_TEXT, '--width', DIALOG_WIDTH, '--entry-text', prefill]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return ''
    return result.stdout.strip()

# Clipboard contents when they look like a key, empty otherwise
def clipboard_key():
    paste = shutil.which('wl-paste')
    if not paste:
        return ''

    # A failed read just means an empty clipboard
    result = subprocess.run([paste, '--no-newline'], capture_output=True, text=True)
    if result.returncode != 0:
        return ''

    # Ignore whatever else was copied
    text = result.stdout.strip()
    if text.startswith(KEY_PREFIX):
        return text
    return ''

# Ask OpenAI whether the key works
def check_key(key):
    # Warn but carry on when offline, the key cannot be checked without the net
    if not utils.network_available():
        print('No internet, saving the key without checking it.', flush=True)
        return True

    # A listable model list means the key is good
    print('Checking the key...', flush=True)
    request = urllib.request.Request(KEY_CHECK_URL, headers={'Authorization': f'Bearer {key}'})
    try:
        with urllib.request.urlopen(request, timeout=KEY_CHECK_TIMEOUT_SECONDS) as response:
            json.load(response)
        print('Key works.', flush=True)
        return True

    # A 401 is a wrong key, anything else is the network or OpenAI having a bad day
    except urllib.error.HTTPError as error:
        if error.code == 401:
            print('OpenAI rejected that key. Copy it again, it is only shown once.', flush=True)
        else:
            print(f'Could not check the key, OpenAI returned {error.code}.', flush=True)
        return False
    except Exception as error:
        print(f'Could not check the key: {error}', flush=True)
        return False

# Write the key where talk reads it, readable only by this user
def save_key(key):
    path = text_client.OPENAI_ENV_FILE
    with open(path, 'w', encoding='utf-8') as env_file:
        env_file.write(f'{text_client.OPENAI_KEY_ENV_NAME}={key}\n')
    os.chmod(path, 0o600)
    print(f'Saved to {path}', flush=True)

# Hand over to talk in this terminal
def start_talk():
    talk_path = os.path.join(utils.SCRIPT_DIR, 'talk.py')
    os.execv(sys.executable, [sys.executable, talk_path])

# Main
if __name__ == '__main__':
    main()
