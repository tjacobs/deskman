#!/usr/bin/env python3

# Desktop browser OAuth for Google and Sonos cloud accounts

# Imports
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import accounts as accounts_store
import accounts.google as google_account
import accounts.sonos as sonos_account

# Config defaults
DEFAULT_PORT = 8765
DEFAULT_PROVIDER = "google"
CALLBACK_PATH = "/callback"
SUCCESS_HTML = "<html><body><h1>Speak auth complete</h1><p>You can close this tab.</p></body></html>"

# Main
def main():
    # Parse provider and flags, then run browser OAuth into accounts.json
    provider, options = parse_args()
    print(run_browser_auth(provider, options))

# Parse provider and OAuth flags
def parse_args():
    options = {
        "account_id": "",
        "label": "",
        "client_id": "",
        "client_secret": "",
        "key": "",
        "calendar_id": google_account.DEFAULT_CALENDAR_ID,
        "household_id": "",
        "default_group_id": "",
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "port": DEFAULT_PORT,
    }
    words = sys.argv[1:]
    provider = DEFAULT_PROVIDER
    if words and not words[0].startswith("-"):
        provider = words[0]
        words = words[1:]
    if provider not in ("google", "sonos"):
        print("Provider must be google or sonos.")
        print_usage()
        sys.exit(2)
    if provider == "google":
        options["account_id"] = google_account.DEFAULT_ACCOUNT_ID
        options["label"] = google_account.DEFAULT_LABEL
    else:
        options["account_id"] = sonos_account.DEFAULT_CLOUD_ACCOUNT_ID
        options["label"] = sonos_account.DEFAULT_CLOUD_LABEL
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--id" and index + 1 < len(words):
            options["account_id"] = words[index + 1]
            index += 2
            continue
        if word == "--label" and index + 1 < len(words):
            options["label"] = words[index + 1]
            index += 2
            continue
        if word == "--client-id" and index + 1 < len(words):
            options["client_id"] = words[index + 1]
            index += 2
            continue
        if word == "--client-secret" and index + 1 < len(words):
            options["client_secret"] = words[index + 1]
            index += 2
            continue
        if word == "--key" and index + 1 < len(words):
            options["key"] = words[index + 1]
            index += 2
            continue
        if word == "--calendar-id" and index + 1 < len(words):
            options["calendar_id"] = words[index + 1]
            index += 2
            continue
        if word == "--household-id" and index + 1 < len(words):
            options["household_id"] = words[index + 1]
            index += 2
            continue
        if word == "--default-group-id" and index + 1 < len(words):
            options["default_group_id"] = words[index + 1]
            index += 2
            continue
        if word == "--accounts" and index + 1 < len(words):
            options["accounts_path"] = os.path.expanduser(words[index + 1])
            index += 2
            continue
        if word == "--port" and index + 1 < len(words):
            options["port"] = int(words[index + 1])
            index += 2
            continue
        if word in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        print(f"Unknown argument: {word}")
        print_usage()
        sys.exit(2)
    return provider, options

# Print usage help
def print_usage():
    print("Usage: python3 -m accounts.auth_browser google|sonos [flags...]")
    print("  Opens a browser, catches localhost OAuth, writes accounts.json")
    print("  Google Desktop apps need no redirect URI in the console")
    print("  Sonos cloud needs redirect URI http://127.0.0.1:8765/callback")
    print("  --client-id --client-secret --key --calendar-id --household-id --accounts --port")

# Run the browser OAuth flow and save the account
def run_browser_auth(provider, options):
    client_id = options["client_id"] or input("Client ID: ").strip()
    client_secret = options["client_secret"] or input("Client secret: ").strip()
    if not client_id or not client_secret:
        return "Client ID and client secret are required."
    api_key = options["key"]
    if provider == "sonos":
        api_key = api_key or input("Sonos API key: ").strip()
        if not api_key:
            return "Sonos API key is required."
    redirect_uri = f"http://127.0.0.1:{options['port']}{CALLBACK_PATH}"
    if provider == "google":
        auth_url = google_account.build_google_auth_url(client_id, redirect_uri)
    else:
        auth_url = sonos_account.build_sonos_auth_url(client_id, redirect_uri)
    print(f"Opening browser for {provider} auth...")
    print(auth_url)
    code = wait_for_auth_code(options["port"], auth_url)
    if not code:
        return "Did not receive an authorization code."
    tokens = accounts_store.exchange_authorization_code(provider, code, client_id, client_secret, redirect_uri)
    if provider == "google":
        account = build_google_account(options, client_id, client_secret, tokens)
    else:
        account = build_sonos_account(options, client_id, client_secret, api_key, tokens)
    if provider == "google" and not account["auth"]["refresh_token"]:
        return "Google did not return a refresh token. Revoke prior access and retry."
    accounts_store.upsert_account(account, load=options["accounts_path"], save=options["accounts_path"])
    smoke = smoke_check(provider, options["account_id"])
    hint = f"If this Mac is not the robot, copy with: scp {options['accounts_path']} pi:talk/accounts.json"
    return f"Saved {provider} account {options['account_id']} to {options['accounts_path']}.\n{smoke}\n{hint}"

# Wait on localhost for the OAuth redirect code
def wait_for_auth_code(port, auth_url):
    result = {"code": "", "error": ""}

    # Handle one OAuth redirect on localhost
    class CallbackHandler(BaseHTTPRequestHandler):
        # Capture code from the redirect query string
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parsed.query)
            result["code"] = (query.get("code") or [""])[0]
            result["error"] = (query.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_HTML.encode("utf-8"))

        # Keep the callback server quiet
        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(auth_url)
    thread.join(timeout=300)
    server.server_close()
    if result["error"]:
        raise SystemExit(f"OAuth error: {result['error']}")
    return result["code"]

# Build the google account payload
def build_google_account(options, client_id, client_secret, tokens):
    return {
        "id": options["account_id"],
        "provider": "google",
        "label": options["label"],
        "enabled": True,
        "auth": {
            "type": "oauth2",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens.get("refresh_token") or "",
            "access_token": tokens.get("access_token") or "",
            "expires_at": tokens.get("expires_at") or "",
        },
        "config": {"calendar_id": options["calendar_id"]},
    }

# Build the Sonos cloud account payload
def build_sonos_account(options, client_id, client_secret, api_key, tokens):
    return {
        "id": options["account_id"],
        "provider": "sonos",
        "mode": "cloud",
        "label": options["label"],
        "enabled": True,
        "auth": {
            "type": "oauth2",
            "client_id": client_id,
            "client_secret": client_secret,
            "key": api_key,
            "refresh_token": tokens.get("refresh_token") or "",
            "access_token": tokens.get("access_token") or "",
            "expires_at": tokens.get("expires_at") or "",
        },
        "config": {
            "household_id": options["household_id"],
            "default_group_id": options["default_group_id"],
        },
    }

# Smoke-check the saved account without talking
def smoke_check(provider, account_id):
    if provider == "google":
        return google_account.run_get_next_calendar_event({"account_id": account_id})
    return sonos_account.run_list_sonos_speakers({"account_id": account_id})

# Main
if __name__ == "__main__":
    main()
