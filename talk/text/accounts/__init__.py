#!/usr/bin/env python3

# Shared accounts.json load, save, and OAuth token refresh

# Imports
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEXT_DIR = os.path.dirname(SCRIPT_DIR)
SPEAK_DIR = os.path.dirname(TEXT_DIR)
ACCOUNTS_PATH = os.path.join(SPEAK_DIR, "accounts.json")

# Config OAuth token endpoints
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SONOS_TOKEN_URL = "https://api.sonos.com/login/v3/oauth/access"
TOKEN_SKEW_SECONDS = 60

# Main
def main():
    # Parse args then list configured accounts with no secrets
    parse_args()
    print(format_accounts_summary(load_accounts()))

# Parse unused args so the module can run with no flags
def parse_args():
    return None

# Load the accounts list from disk
def load_accounts(load=None):
    path = load if load else ACCOUNTS_PATH
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as accounts_file:
            data = json.load(accounts_file)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return []
    return [account for account in accounts if isinstance(account, dict) and str(account.get("id") or "").strip()]

# Save the accounts list to disk atomically
def save_accounts(accounts, save=None):
    path = save if save else ACCOUNTS_PATH
    payload = {"accounts": accounts}
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as accounts_file:
        json.dump(payload, accounts_file, indent=2)
        accounts_file.write("\n")
    os.replace(temporary_path, path)

# Return enabled accounts for a provider, optionally filtered by mode
def get_accounts(provider, mode=None, load=None):
    matches = []
    for account in load_accounts(load):
        if not account.get("enabled", True):
            continue
        if str(account.get("provider") or "").strip() != provider:
            continue
        if mode is not None and str(account.get("mode") or "").strip() != mode:
            continue
        matches.append(account)
    return matches

# Return the first matching enabled account, or None
def get_account(provider, mode=None, account_id=None, load=None):
    for account in get_accounts(provider, mode, load):
        if account_id is not None and str(account.get("id") or "") != account_id:
            continue
        return account
    return None

# Insert or replace one account by id and save
def upsert_account(account, load=None, save=None):
    path_load = load if load else ACCOUNTS_PATH
    path_save = save if save else path_load
    accounts = load_accounts(path_load)
    account_id = str(account.get("id") or "").strip()
    if not account_id:
        raise ValueError("Account id is required.")
    kept = [item for item in accounts if str(item.get("id") or "") != account_id]
    kept.append(account)
    save_accounts(kept, path_save)
    return account

# Remove one account by id and save
def remove_account(account_id, load=None, save=None):
    path_load = load if load else ACCOUNTS_PATH
    path_save = save if save else path_load
    accounts = load_accounts(path_load)
    kept = [item for item in accounts if str(item.get("id") or "") != account_id]
    if len(kept) == len(accounts):
        return False
    save_accounts(kept, path_save)
    return True

# Refresh OAuth access token when missing or near expiry, then persist
def ensure_access_token(account, load=None, save=None):
    auth = account.get("auth") or {}
    if str(auth.get("type") or "") != "oauth2":
        return account
    if access_token_is_fresh(auth):
        return account
    provider = str(account.get("provider") or "").strip()
    tokens = refresh_oauth_token(provider, auth)
    auth["access_token"] = tokens.get("access_token") or ""
    auth["expires_at"] = tokens.get("expires_at") or ""
    if tokens.get("refresh_token"):
        auth["refresh_token"] = tokens["refresh_token"]
    account["auth"] = auth
    upsert_account(account, load, save)
    return account

# Return true when the stored access token is still usable
def access_token_is_fresh(auth):
    access_token = str(auth.get("access_token") or "").strip()
    expires_at = str(auth.get("expires_at") or "").strip()
    if not access_token or not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > datetime.now(timezone.utc) + timedelta(seconds=TOKEN_SKEW_SECONDS)

# Exchange a refresh token for a new access token
def refresh_oauth_token(provider, auth):
    refresh_token = str(auth.get("refresh_token") or "").strip()
    client_id = str(auth.get("client_id") or "").strip()
    client_secret = str(auth.get("client_secret") or "").strip()
    if not refresh_token or not client_id or not client_secret:
        raise ValueError("OAuth client id, client secret, and refresh token are required.")
    if provider == "google":
        return exchange_google_token({"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id, "client_secret": client_secret})
    if provider == "sonos":
        return exchange_sonos_token({"grant_type": "refresh_token", "refresh_token": refresh_token}, client_id, client_secret)
    raise ValueError(f"Unsupported OAuth provider {provider}.")

# Exchange an authorization code for tokens
def exchange_authorization_code(provider, code, client_id, client_secret, redirect_uri):
    if provider == "google":
        return exchange_google_token({"grant_type": "authorization_code", "code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri})
    if provider == "sonos":
        return exchange_sonos_token({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}, client_id, client_secret)
    raise ValueError(f"Unsupported OAuth provider {provider}.")

# POST to Google token endpoint
def exchange_google_token(fields):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(GOOGLE_TOKEN_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    return parse_token_response(request)

# POST to Sonos token endpoint with basic auth
def exchange_sonos_token(fields, client_id, client_secret):
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(SONOS_TOKEN_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded;charset=utf-8")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {basic}")
    return parse_token_response(request)

# Read token JSON and normalize expiry
def parse_token_response(request):
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ValueError(f"Token request failed: {detail}") from error
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise ValueError("Token response did not include an access token.")
    expires_in = int(payload.get("expires_in") or 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or "").strip(),
        "expires_at": expires_at.isoformat(),
    }

# Format a short no-secrets summary for the CLI
def format_accounts_summary(accounts, load=None):
    path = load if load else ACCOUNTS_PATH
    if not accounts:
        return f"No accounts in {path}."
    lines = [f"Accounts in {path}:"]
    for account in accounts:
        account_id = account.get("id", "")
        provider = account.get("provider", "")
        mode = account.get("mode") or "-"
        enabled = "on" if account.get("enabled", True) else "off"
        label = account.get("label") or ""
        lines.append(f"- {account_id}: {provider} mode={mode} enabled={enabled} {label}".rstrip())
    return "\n".join(lines)

# Main
if __name__ == "__main__":
    main()
