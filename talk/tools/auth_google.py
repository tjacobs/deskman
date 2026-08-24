#!.venv/bin/python

# Google Calendar setup, prints steps and opens the browser

# Imports
import os
import sys

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
TEXT_DIR = os.path.join(SPEAK_DIR, "text")

# Main
def main():
    # Put text/ on the import path for accounts modules
    ensure_text_path()

    # Parse unused args so ./tools/auth_google.py works with no flags
    parse_args()

    # Print the Google Cloud steps, then run browser OAuth
    print_google_setup_steps()
    import accounts as accounts_store
    import accounts.auth_browser as auth_browser
    import accounts.google as google_account
    options = {
        "account_id": google_account.DEFAULT_ACCOUNT_ID,
        "label": google_account.DEFAULT_LABEL,
        "client_id": "",
        "client_secret": "",
        "key": "",
        "calendar_id": google_account.DEFAULT_CALENDAR_ID,
        "household_id": "",
        "default_group_id": "",
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "port": auth_browser.DEFAULT_PORT,
    }
    print(auth_browser.run_browser_auth("google", options))

# Parse help-only flags
def parse_args():
    for word in sys.argv[1:]:
        if word in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        print(f"Unknown argument: {word}")
        print_usage()
        sys.exit(2)

# Print usage help
def print_usage():
    print("Usage: ./tools/auth_google.py")
    print("  Prints Google Calendar setup steps, opens the browser, writes accounts.json")

# Explain Google Cloud Console setup before prompting
def print_google_setup_steps():
    print("Google Calendar setup")
    print(f"This will write {os.path.join(SPEAK_DIR, 'accounts.json')}.")
    print("")
    print("1. Open https://console.cloud.google.com/ (cmd-click)")
    print("2. Create or select a project")
    print("3. Enable the Google Calendar API by searching \"Calendar\"")
    print("4. Create credentials, as a Desktop app")
    print("5. Add scopes: all calendar read writes https://console.cloud.google.com/auth/scopes (cmd-click)")
    print("6. Publish app at https://console.cloud.google.com/auth/audience (cmd-click)")
    print("7. Open app details, copy the client id and client secret")
    print("")

# Ensure text/ is importable
def ensure_text_path():
    if TEXT_DIR not in sys.path:
        sys.path.insert(0, TEXT_DIR)

# Main
if __name__ == "__main__":
    main()
