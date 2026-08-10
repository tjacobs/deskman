#!.venv/bin/python

# One-line Sonos setup, guides LAN discovery or cloud browser auth

# Imports
import os
import sys

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEXT_DIR = os.path.join(SCRIPT_DIR, "text")
REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_MODE = "lan"

# Main
def main():
    # Put text/ on the import path for accounts modules
    ensure_text_path()

    # Parse optional mode, then guide LAN or cloud setup
    mode = parse_args()
    if mode == "lan":
        run_lan_guide()
        return
    run_cloud_guide()

# Parse lan/cloud mode or ask interactively
def parse_args():
    words = sys.argv[1:]
    if not words:
        return ask_mode()
    if words[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0)
    if words[0] in ("lan", "local", "cloud"):
        mode = "lan" if words[0] in ("lan", "local") else "cloud"
        if len(words) > 1:
            print(f"Unknown argument: {words[1]}")
            print_usage()
            sys.exit(2)
        return mode
    print(f"Unknown argument: {words[0]}")
    print_usage()
    sys.exit(2)

# Print usage help
def print_usage():
    print("Usage: ./auth_sonos.py [lan|cloud]")
    print("  No args: ask lan or cloud, then guide setup into accounts.json")
    print("  lan      discover Sonos speakers on this WiFi")
    print("  cloud    open browser OAuth for Sonos Control API")

# Ask whether to set up LAN or cloud
def ask_mode():
    print("Sonos setup")
    print(f"This will write {os.path.join(SCRIPT_DIR, 'accounts.json')}.")
    print("")
    print("Choose setup mode:")
    print("  1. lan    same WiFi as speakers, no Sonos developer account")
    print("  2. cloud  Sonos Control API with browser sign-in")
    print("")
    answer = input(f"Mode [1=lan / 2=cloud, default {DEFAULT_MODE}]: ").strip().lower()
    if answer in ("", "1", "lan", "local"):
        return "lan"
    if answer in ("2", "cloud"):
        return "cloud"
    print("Please choose lan or cloud.")
    sys.exit(2)

# Run guided LAN speaker discovery
def run_lan_guide():
    import accounts as accounts_store
    import accounts.auth_sonos_lan as auth_sonos_lan
    options = {
        "account_id": auth_sonos_lan.DEFAULT_ACCOUNT_ID,
        "label": auth_sonos_lan.DEFAULT_LABEL,
        "default_speaker": "",
        "speakers": [],
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "smoke": None,
    }
    print(auth_sonos_lan.run_lan_setup(options))

# Print Sonos developer steps, then run browser OAuth
def run_cloud_guide():
    print_sonos_cloud_steps()
    import accounts as accounts_store
    import accounts.auth_browser as auth_browser
    import accounts.sonos as sonos_account
    options = {
        "account_id": sonos_account.DEFAULT_CLOUD_ACCOUNT_ID,
        "label": sonos_account.DEFAULT_CLOUD_LABEL,
        "client_id": "",
        "client_secret": "",
        "key": "",
        "calendar_id": "",
        "household_id": "",
        "default_group_id": "",
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "port": auth_browser.DEFAULT_PORT,
    }
    print(auth_browser.run_browser_auth("sonos", options))

# Explain Sonos Control API setup before prompting
def print_sonos_cloud_steps():
    print("Sonos cloud setup")
    print(f"This will write {os.path.join(SCRIPT_DIR, 'accounts.json')}.")
    print("")
    print("1. Create a Sonos Control API integration and get client id, secret, and API key")
    print(f"2. Add redirect URI: {REDIRECT_URI}")
    print("3. Copy those values when prompted")
    print("")
    print("Then sign in when the browser opens.")
    print("")

# Ensure text/ is importable
def ensure_text_path():
    if TEXT_DIR not in sys.path:
        sys.path.insert(0, TEXT_DIR)

# Main
if __name__ == "__main__":
    main()
