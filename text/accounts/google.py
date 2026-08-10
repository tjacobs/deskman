#!/usr/bin/env python3

# Google Calendar tools and paste-code OAuth setup

# Imports
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import accounts as accounts_store

# Config defaults
DEFAULT_ACCOUNT_ID = "google-home"
DEFAULT_LABEL = "Home Google"
DEFAULT_CALENDAR_ID = "primary"
DEFAULT_COMMAND = "next"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
NOT_CONFIGURED = "Google account not configured."
REDIRECT_URI_PASTE = "urn:ietf:wg:oauth:2.0:oob"
MAX_EVENTS = 8

# Tools the local model can call for Google Calendar
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "List Google Calendar events for today, tomorrow, or a YYYY-MM-DD date. Never invent events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "day": {
                        "type": "string",
                        "description": "today, tomorrow, or YYYY-MM-DD.",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional Google account id from accounts.json.",
                    },
                },
                "required": ["day"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_calendar_event",
            "description": "Get the next upcoming Google Calendar event. Never invent events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": "Optional Google account id from accounts.json.",
                    },
                },
            },
        },
    },
]

# Main
def main():
    # Parse command line and run the chosen setup or smoke action
    command, options = parse_args()
    if command == "add":
        print(add_google_account(options))
        return
    if command == "list":
        print(accounts_store.format_accounts_summary(accounts_store.get_accounts("google", load=options["accounts_path"]), load=options["accounts_path"]))
        return
    if command == "remove":
        removed = accounts_store.remove_account(options["account_id"], load=options["accounts_path"], save=options["accounts_path"])
        print(f"Removed {options['account_id']}." if removed else f"No account {options['account_id']}.")
        return
    print(run_get_next_calendar_event({"account_id": options.get("account_id") or ""}))

# Parse add, list, remove, or default next-event flags
def parse_args():
    command = DEFAULT_COMMAND
    options = {
        "account_id": DEFAULT_ACCOUNT_ID,
        "label": DEFAULT_LABEL,
        "client_id": "",
        "client_secret": "",
        "calendar_id": DEFAULT_CALENDAR_ID,
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "code": "",
    }
    words = sys.argv[1:]
    if words and words[0] in ("add", "list", "remove", "next"):
        command = words[0]
        words = words[1:]
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
        if word == "--calendar-id" and index + 1 < len(words):
            options["calendar_id"] = words[index + 1]
            index += 2
            continue
        if word == "--accounts" and index + 1 < len(words):
            options["accounts_path"] = os.path.expanduser(words[index + 1])
            index += 2
            continue
        if word == "--code" and index + 1 < len(words):
            options["code"] = words[index + 1]
            index += 2
            continue
        if word in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        print(f"Unknown argument: {word}")
        print_usage()
        sys.exit(2)
    return command, options

# Print usage help
def print_usage():
    print("Usage: python3 -m accounts.google [next|add|list|remove] [flags...]")
    print("  next             print the next calendar event, default with no args")
    print("  add              paste-code OAuth setup into accounts.json")
    print("  list             list google accounts")
    print("  remove --id ID   remove one account")
    print("  --client-id --client-secret --calendar-id --accounts --code")

# List events for a day from tool arguments
def run_list_calendar_events(arguments):
    account = resolve_google_account(arguments)
    if account is None:
        return NOT_CONFIGURED
    day = str(arguments.get("day") or "today").strip().lower()
    try:
        start, end = day_bounds(day)
        events = fetch_calendar_events(account, start, end, MAX_EVENTS)
    except (ValueError, urllib.error.URLError, OSError) as error:
        return f"Could not read Google Calendar: {error}"
    if not events:
        return f"No Google Calendar events for {day}."
    lines = [format_event_line(event) for event in events]
    return f"Google Calendar for {day}: " + "; ".join(lines)

# Return the next upcoming event from tool arguments
def run_get_next_calendar_event(arguments):
    account = resolve_google_account(arguments)
    if account is None:
        return NOT_CONFIGURED
    try:
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=30)
        events = fetch_calendar_events(account, start, end, 1)
    except (ValueError, urllib.error.URLError, OSError) as error:
        return f"Could not read Google Calendar: {error}"
    if not events:
        return "No upcoming Google Calendar events."
    return "Next Google Calendar event: " + format_event_line(events[0])

# Resolve and refresh the google account used by a tool call
def resolve_google_account(arguments):
    account_id = str(arguments.get("account_id") or "").strip() or None
    account = accounts_store.get_account("google", account_id=account_id)
    if account is None:
        return None
    return accounts_store.ensure_access_token(account)

# Fetch events between start and end
def fetch_calendar_events(account, start, end, max_results):
    auth = account.get("auth") or {}
    config = account.get("config") or {}
    calendar_id = str(config.get("calendar_id") or DEFAULT_CALENDAR_ID).strip() or DEFAULT_CALENDAR_ID
    query = urllib.parse.urlencode({
        "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max_results),
    })
    url = GOOGLE_EVENTS_URL.format(calendar_id=urllib.parse.quote(calendar_id, safe="@")) + "?" + query
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {auth.get('access_token')}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ValueError(detail) from error
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return items

# Turn a calendar day word into UTC start and end
def day_bounds(day):
    local_now = datetime.now().astimezone()
    if day in ("", "today"):
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif day == "tomorrow":
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        start_local = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=local_now.tzinfo)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local

# Format one event for speech
def format_event_line(event):
    summary = str(event.get("summary") or "Untitled").strip()
    start = event.get("start") or {}
    if start.get("dateTime"):
        when = datetime.fromisoformat(start["dateTime"]).astimezone().strftime("%I:%M %p").lstrip("0")
    elif start.get("date"):
        when = "all day"
    else:
        when = "unknown time"
    return f"{summary} at {when}"

# Interactive paste-code OAuth setup
def add_google_account(options):
    client_id = options["client_id"] or input("Google client id: ").strip()
    client_secret = options["client_secret"] or input("Google client secret: ").strip()
    if not client_id or not client_secret:
        return "Google client id and client secret are required."
    auth_url = build_google_auth_url(client_id, REDIRECT_URI_PASTE)
    print("Open this URL, sign in, then paste the code:")
    print(auth_url)
    code = options["code"] or input("Code: ").strip()
    if not code:
        return "Authorization code is required."
    tokens = accounts_store.exchange_authorization_code("google", code, client_id, client_secret, REDIRECT_URI_PASTE)
    account = {
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
    if not account["auth"]["refresh_token"]:
        return "Google did not return a refresh token. Revoke prior access and try again with prompt=consent."
    accounts_store.upsert_account(account, load=options["accounts_path"], save=options["accounts_path"])
    smoke = run_get_next_calendar_event({"account_id": options["account_id"]})
    return f"Saved Google account {options['account_id']} to {options['accounts_path']}.\n{smoke}"

# Build the Google authorize URL for paste-code flow
def build_google_auth_url(client_id, redirect_uri):
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    return GOOGLE_AUTH_URL + "?" + query

# Main
if __name__ == "__main__":
    main()
