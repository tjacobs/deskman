#!/usr/bin/env python3

# Sonos local LAN and cloud Control API tools

# Imports
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import accounts as accounts_store

# Config defaults
DEFAULT_LOCAL_ACCOUNT_ID = "sonos-local"
DEFAULT_CLOUD_ACCOUNT_ID = "sonos-cloud"
DEFAULT_LOCAL_LABEL = "Sonos LAN"
DEFAULT_CLOUD_LABEL = "Sonos cloud"
DEFAULT_COMMAND = "list"
NOT_CONFIGURED = "Sonos not configured."
SONOS_AUTH_URL = "https://api.sonos.com/login/v3/oauth"
SONOS_CONTROL_BASE = "https://api.ws.sonos.com/control/api/v1"
SONOS_SCOPE = "playback-control-all"
REDIRECT_URI_PASTE = "http://localhost"
MAX_VOLUME = 100
SONOS_MENTION = r"\b(sonos|sono'?s|sonar)\b"
PAUSE_SONOS_RETRY_PROMPT = "Do not guess. Call pause_sonos now. Omit room to use the default speaker, or pass room all for every room, then answer using only the tool result."
PLAY_SONOS_RETRY_PROMPT = "Do not guess. Call play_sonos now. Omit room to use the default speaker, then answer using only the tool result."
SET_SONOS_VOLUME_RETRY_PROMPT = "Do not guess. Call set_sonos_volume now with the requested percent. Omit room to use the default speaker, then answer using only the tool result."

# Tools the local model can call for Sonos
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_sonos_speakers",
            "description": "List Sonos speakers or groups available to play music.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": "Optional Sonos account id from accounts.json.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_sonos",
            "description": "Play or resume Sonos. Omit room to use the default speaker. Pass room all for every room. Optional URI to play.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": "Optional speaker room, group name, or all.",
                    },
                    "uri": {
                        "type": "string",
                        "description": "Optional audio URI to play.",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional Sonos account id from accounts.json.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_sonos",
            "description": "Pause Sonos. Omit room to use the default speaker. Pass room all for every room.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": "Optional speaker room, group name, or all.",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional Sonos account id from accounts.json.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_sonos_volume",
            "description": "Set Sonos volume to a percent from 0 to 100. Omit room to use the default speaker. Pass room all for every room.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "number",
                        "description": "Volume percent from 0 to 100.",
                    },
                    "room": {
                        "type": "string",
                        "description": "Optional speaker room, group name, or all.",
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional Sonos account id from accounts.json.",
                    },
                },
                "required": ["percent"],
            },
        },
    },
]

# Main
def main():
    # Parse command line and run list, add, or remove
    command, options = parse_args()
    if command == "add":
        print(add_sonos_account(options))
        return
    if command == "list-accounts":
        print(accounts_store.format_accounts_summary(accounts_store.get_accounts("sonos", load=options["accounts_path"]), load=options["accounts_path"]))
        return
    if command == "remove":
        removed = accounts_store.remove_account(options["account_id"], load=options["accounts_path"], save=options["accounts_path"])
        print(f"Removed {options['account_id']}." if removed else f"No account {options['account_id']}.")
        return
    print(run_list_sonos_speakers({"account_id": options.get("account_id") or ""}))

# Parse add, list, remove, or default speaker list flags
def parse_args():
    command = DEFAULT_COMMAND
    options = {
        "account_id": DEFAULT_LOCAL_ACCOUNT_ID,
        "label": DEFAULT_LOCAL_LABEL,
        "mode": "local",
        "client_id": "",
        "client_secret": "",
        "key": "",
        "household_id": "",
        "default_group_id": "",
        "default_speaker": "",
        "speakers": [],
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "code": "",
    }
    words = sys.argv[1:]
    if words and words[0] in ("add", "list", "list-accounts", "remove"):
        command = words[0]
        words = words[1:]
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--mode" and index + 1 < len(words):
            options["mode"] = words[index + 1]
            if options["mode"] == "cloud":
                options["account_id"] = DEFAULT_CLOUD_ACCOUNT_ID
                options["label"] = DEFAULT_CLOUD_LABEL
            index += 2
            continue
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
        if word == "--household-id" and index + 1 < len(words):
            options["household_id"] = words[index + 1]
            index += 2
            continue
        if word == "--default-group-id" and index + 1 < len(words):
            options["default_group_id"] = words[index + 1]
            index += 2
            continue
        if word == "--default-speaker" and index + 1 < len(words):
            options["default_speaker"] = words[index + 1]
            index += 2
            continue
        if word == "--speaker" and index + 1 < len(words):
            options["speakers"].append(parse_speaker_flag(words[index + 1]))
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
    print("Usage: python3 -m accounts.sonos [list|add|list-accounts|remove] [flags...]")
    print("  list             list speakers or groups, default with no args")
    print("  add --mode local|cloud   write a Sonos account into accounts.json")
    print("  list-accounts    list Sonos accounts")
    print("  remove --id ID   remove one account")
    print("  Prefer python3 -m accounts.auth_sonos_lan for guided LAN setup")
    print("  Prefer python3 -m accounts.auth_browser sonos for cloud browser OAuth")

# Parse Name=IP speaker flag
def parse_speaker_flag(text):
    if "=" not in text:
        raise SystemExit(f"Speaker must look like Name=IP, got {text}")
    name, ip_address = text.split("=", 1)
    return {"name": name.strip(), "ip": ip_address.strip()}

# List speakers or groups from tool arguments
def run_list_sonos_speakers(arguments):
    account = resolve_sonos_account(arguments, prefer_local=True)
    if account is None:
        return NOT_CONFIGURED
    mode = str(account.get("mode") or "")
    try:
        if mode == "local":
            return format_local_speakers(list_local_speakers(account))
        return format_cloud_groups(list_cloud_groups(account))
    except (ValueError, urllib.error.URLError, OSError, ImportError) as error:
        return f"Could not list Sonos: {error}"

# Play or resume from tool arguments
def run_play_sonos(arguments):
    account = resolve_sonos_account(arguments, prefer_local=True)
    if account is None:
        return NOT_CONFIGURED
    room = str(arguments.get("room") or "").strip()
    uri = str(arguments.get("uri") or "").strip()
    mode = str(account.get("mode") or "")
    try:
        if mode == "local":
            return play_local(account, room, uri)
        return play_cloud(account, room, uri)
    except (ValueError, urllib.error.URLError, OSError, ImportError) as error:
        return f"Could not play Sonos: {error}"

# Pause from tool arguments
def run_pause_sonos(arguments):
    account = resolve_sonos_account(arguments, prefer_local=True)
    if account is None:
        return NOT_CONFIGURED
    room = str(arguments.get("room") or "").strip()
    mode = str(account.get("mode") or "")
    try:
        if mode == "local":
            return pause_local(account, room)
        return pause_cloud(account, room)
    except (ValueError, urllib.error.URLError, OSError, ImportError) as error:
        return f"Could not pause Sonos: {error}"

# Set volume from tool arguments
def run_set_sonos_volume(arguments):
    account = resolve_sonos_account(arguments, prefer_local=True)
    if account is None:
        return NOT_CONFIGURED
    if "percent" not in arguments:
        return "Volume percent is required."
    try:
        percent = int(round(float(arguments["percent"])))
    except (TypeError, ValueError):
        return "Volume percent must be a number."
    percent = max(0, min(MAX_VOLUME, percent))
    room = str(arguments.get("room") or "").strip()
    mode = str(account.get("mode") or "")
    try:
        if mode == "local":
            return set_local_volume(account, room, percent)
        return set_cloud_volume(account, room, percent)
    except (ValueError, urllib.error.URLError, OSError, ImportError) as error:
        return f"Could not set Sonos volume: {error}"

# Return true when the prompt mentions Sonos, including common mishearings
def mentions_sonos(prompt):
    return bool(re.search(SONOS_MENTION, prompt.lower()))

# Return true when the user wants Sonos volume changed
def needs_set_sonos_volume(prompt):
    text = prompt.lower()
    if not mentions_sonos(prompt):
        return False
    if "volume" not in text:
        return False
    if re.search(r"\d+\s*%", text):
        return True
    return bool(re.search(r"\b(set|change|make|turn|raise|lower)\b", text))

# Return true when the user wants Sonos paused
def needs_pause_sonos(prompt):
    text = prompt.lower()
    if not mentions_sonos(prompt):
        return False
    return bool(re.search(r"\b(pause|stop)\b", text))

# Return true when the user wants Sonos to play
def needs_play_sonos(prompt):
    text = prompt.lower()
    if not mentions_sonos(prompt):
        return False
    if needs_pause_sonos(prompt) or needs_set_sonos_volume(prompt):
        return False
    return bool(re.search(r"\b(play|resume|unpause)\b", text))

# Retry or apply set_sonos_volume when the model skipped it
def force_set_sonos_volume(prompt, messages, message, already_retried, record_tool):
    percent = parse_sonos_volume_percent(prompt)
    room = parse_sonos_room(prompt)
    if not already_retried:
        print("[sonos] missing set_sonos_volume, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if percent is None:
            retry = SET_SONOS_VOLUME_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call set_sonos_volume with percent {percent} now. Omit room to use the default speaker, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True
    if percent is None:
        return None
    arguments = {"percent": percent}
    if room:
        arguments["room"] = room
    result = run_set_sonos_volume(arguments)
    record_tool("set_sonos_volume", arguments, result)
    print(f"[sonos] forced set_sonos_volume -> {result}", flush=True)
    return result

# Retry or apply pause_sonos when the model skipped it
def force_pause_sonos(prompt, messages, message, already_retried, record_tool):
    room = parse_sonos_room(prompt)
    if not already_retried:
        print("[sonos] missing pause_sonos, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": PAUSE_SONOS_RETRY_PROMPT})
        return True
    arguments = {}
    if room:
        arguments["room"] = room
    result = run_pause_sonos(arguments)
    record_tool("pause_sonos", arguments, result)
    print(f"[sonos] forced pause_sonos -> {result}", flush=True)
    return result

# Retry or apply play_sonos when the model skipped it
def force_play_sonos(prompt, messages, message, already_retried, record_tool):
    room = parse_sonos_room(prompt)
    if not already_retried:
        print("[sonos] missing play_sonos, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": PLAY_SONOS_RETRY_PROMPT})
        return True
    arguments = {}
    if room:
        arguments["room"] = room
    result = run_play_sonos(arguments)
    record_tool("play_sonos", arguments, result)
    print(f"[sonos] forced play_sonos -> {result}", flush=True)
    return result

# Read a Sonos volume percent from the user text
def parse_sonos_volume_percent(prompt):
    match = re.search(r"(\d+)\s*%", prompt.lower())
    if match:
        return max(0, min(MAX_VOLUME, int(match.group(1))))
    match = re.search(r"\b(?:to|at)\s+(\d+)\b", prompt.lower())
    if match:
        return max(0, min(MAX_VOLUME, int(match.group(1))))
    return None

# Read all-rooms or leave blank for the default speaker
def parse_sonos_room(prompt):
    text = prompt.lower()
    if re.search(r"\ball rooms?\b|\beverywhere\b|\bevery room\b", text):
        return "all"
    return ""

# Return true when room means every configured speaker
def is_all_rooms(room):
    text = str(room or "").strip().lower()
    return text in ("all", "all rooms", "everywhere", "every room")

# Pick a Sonos account, preferring local when both exist
def resolve_sonos_account(arguments, prefer_local):
    account_id = str(arguments.get("account_id") or "").strip() or None
    if account_id:
        account = accounts_store.get_account("sonos", account_id=account_id)
        if account is None:
            return None
        if str(account.get("mode") or "") == "cloud":
            return accounts_store.ensure_access_token(account)
        return account
    local_account = accounts_store.get_account("sonos", mode="local")
    cloud_account = accounts_store.get_account("sonos", mode="cloud")
    if prefer_local and local_account is not None:
        return local_account
    if cloud_account is not None:
        return accounts_store.ensure_access_token(cloud_account)
    return local_account

# Build local speaker rows from account config
def list_local_speakers(account):
    config = account.get("config") or {}
    speakers = config.get("speakers")
    if not isinstance(speakers, list):
        return []
    rows = []
    for speaker in speakers:
        if not isinstance(speaker, dict):
            continue
        name = str(speaker.get("name") or "").strip()
        ip_address = str(speaker.get("ip") or "").strip()
        if name and ip_address:
            rows.append({"name": name, "ip": ip_address})
    return rows

# Format local speakers for speech
def format_local_speakers(speakers):
    if not speakers:
        return "No Sonos LAN speakers configured."
    return "Sonos speakers: " + ", ".join(f"{row['name']} at {row['ip']}" for row in speakers)

# Play on a local speaker or every configured speaker
def play_local(account, room, uri):
    if is_all_rooms(room):
        names = []
        for device in local_devices(account):
            if uri:
                device.play_uri(uri)
            else:
                device.play()
            names.append(device.player_name)
        return "Playing on " + ", ".join(names) + "."
    device = local_device(account, room)
    if uri:
        device.play_uri(uri)
        return f"Playing on {device.player_name}."
    device.play()
    return f"Playing on {device.player_name}."

# Pause a local speaker or every configured speaker
def pause_local(account, room):
    if is_all_rooms(room):
        names = []
        for device in local_devices(account):
            device.pause()
            names.append(device.player_name)
        return "Paused " + ", ".join(names) + "."
    device = local_device(account, room)
    device.pause()
    return f"Paused {device.player_name}."

# Set volume on a local speaker or every configured speaker
def set_local_volume(account, room, percent):
    if is_all_rooms(room):
        names = []
        for device in local_devices(account):
            device.volume = percent
            names.append(device.player_name)
        return f"Set {', '.join(names)} volume to {percent} percent."
    device = local_device(account, room)
    device.volume = percent
    return f"Set {device.player_name} volume to {percent} percent."

# Connect to one local speaker by room name
def local_device(account, room):
    soco_module = import_soco()
    speakers = list_local_speakers(account)
    if not speakers:
        raise ValueError("No Sonos LAN speakers configured.")
    config = account.get("config") or {}
    default_speaker = str(config.get("default_speaker") or "").strip()
    target_name = room or default_speaker or speakers[0]["name"]
    for speaker in speakers:
        if speaker["name"].lower() == target_name.lower():
            return soco_module.SoCo(speaker["ip"])
    raise ValueError(f"Sonos speaker {target_name} not found.")

# Connect to every configured local speaker
def local_devices(account):
    soco_module = import_soco()
    speakers = list_local_speakers(account)
    if not speakers:
        raise ValueError("No Sonos LAN speakers configured.")
    return [soco_module.SoCo(speaker["ip"]) for speaker in speakers]

# Import SoCo, installing it into the speak venv when missing
def import_soco():
    ensure_soco_installed()
    try:
        import soco
    except ImportError as error:
        raise ImportError("soco is not installed. Run ./install.sh or uv pip install soco.") from error
    return soco

# Install soco into the speak venv when missing
def ensure_soco_installed():
    try:
        import soco
        return
    except ImportError:
        pass
    speak_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    python_path = os.path.join(speak_dir, ".venv", "bin", "python")
    if not os.path.isfile(python_path):
        python_path = sys.executable
    print("Installing soco with: uv pip install soco", flush=True)
    result = subprocess.run(["uv", "pip", "install", "--python", python_path, "soco"], check=False)
    if result.returncode != 0:
        raise ImportError("Could not install soco. Run: uv pip install soco")
    try:
        import importlib
        importlib.invalidate_caches()
        import soco
    except ImportError as error:
        raise ImportError("soco installed, but this process still cannot import it. Restart talk.py.") from error

# List cloud groups for an account
def list_cloud_groups(account):
    household_id = cloud_household_id(account)
    payload = sonos_cloud_request(account, "GET", f"/households/{household_id}/groups")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        return []
    return groups

# Format cloud groups for speech
def format_cloud_groups(groups):
    if not groups:
        return "No Sonos cloud groups found."
    names = [str(group.get("name") or group.get("id") or "group") for group in groups]
    return "Sonos groups: " + ", ".join(names)

# Play on a cloud group
def play_cloud(account, room, uri):
    group_id = cloud_group_id(account, room)
    if uri:
        body = {"streamUrl": uri, "playOnCompletion": True}
        sonos_cloud_request(account, "POST", f"/groups/{group_id}/playback/loadStreamUrl", body)
        return f"Playing on Sonos group {group_id}."
    sonos_cloud_request(account, "POST", f"/groups/{group_id}/playback/play")
    return f"Playing on Sonos group {group_id}."

# Pause a cloud group
def pause_cloud(account, room):
    group_id = cloud_group_id(account, room)
    sonos_cloud_request(account, "POST", f"/groups/{group_id}/playback/pause")
    return f"Paused Sonos group {group_id}."

# Set volume on a cloud group
def set_cloud_volume(account, room, percent):
    group_id = cloud_group_id(account, room)
    body = {"volume": percent}
    sonos_cloud_request(account, "POST", f"/groups/{group_id}/groupVolume", body)
    return f"Set Sonos group volume to {percent} percent."

# Resolve household id, fetching and saving when missing
def cloud_household_id(account):
    config = account.get("config") or {}
    household_id = str(config.get("household_id") or "").strip()
    if household_id:
        return household_id
    payload = sonos_cloud_request(account, "GET", "/households")
    households = payload.get("households")
    if not isinstance(households, list) or not households:
        raise ValueError("No Sonos households on this account.")
    household_id = str(households[0].get("id") or "").strip()
    if not household_id:
        raise ValueError("Sonos household id missing.")
    config["household_id"] = household_id
    account["config"] = config
    accounts_store.upsert_account(account)
    return household_id

# Resolve group id from room name or defaults
def cloud_group_id(account, room):
    config = account.get("config") or {}
    default_group_id = str(config.get("default_group_id") or "").strip()
    groups = list_cloud_groups(account)
    if not groups:
        raise ValueError("No Sonos cloud groups found.")
    if room:
        for group in groups:
            name = str(group.get("name") or "").strip()
            group_id = str(group.get("id") or "").strip()
            if name.lower() == room.lower() or group_id == room:
                return group_id
        raise ValueError(f"Sonos group {room} not found.")
    if default_group_id:
        return default_group_id
    return str(groups[0].get("id") or "").strip()

# GET or POST the Sonos Control API
def sonos_cloud_request(account, method, path, body=None):
    account = accounts_store.ensure_access_token(account)
    auth = account.get("auth") or {}
    access_token = str(auth.get("access_token") or "").strip()
    api_key = str(auth.get("key") or "").strip()
    if not access_token or not api_key:
        raise ValueError("Sonos cloud access token and API key are required.")
    url = SONOS_CONTROL_BASE + path
    data = None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Sonos-Api-Key": api_key,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method)
    for header_name, header_value in headers.items():
        request.add_header(header_name, header_value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ValueError(detail) from error
    if not raw:
        return {}
    return json.loads(raw)

# CLI add for local or cloud Sonos accounts
def add_sonos_account(options):
    if options["mode"] == "local":
        return add_local_account(options)
    if options["mode"] == "cloud":
        return add_cloud_account(options)
    return "Mode must be local or cloud."

# Write a local LAN account from flags or discovery
def add_local_account(options):
    speakers = options["speakers"]
    if not speakers:
        speakers = discover_local_speakers()
    if not speakers:
        return "No Sonos speakers found. Pass --speaker Name=IP or run python3 -m accounts.auth_sonos_lan."
    default_speaker = options["default_speaker"] or speakers[0]["name"]
    account = {
        "id": options["account_id"],
        "provider": "sonos",
        "mode": "local",
        "label": options["label"],
        "enabled": True,
        "auth": {"type": "local"},
        "config": {"default_speaker": default_speaker, "speakers": speakers},
    }
    accounts_store.upsert_account(account, load=options["accounts_path"], save=options["accounts_path"])
    return f"Saved Sonos LAN account {options['account_id']} with {len(speakers)} speakers to {options['accounts_path']}."

# Discover Sonos speakers on the LAN
def discover_local_speakers():
    soco = import_soco()
    rows = []
    for device in soco.discover(timeout=5) or []:
        rows.append({"name": str(device.player_name), "ip": str(device.ip_address)})
    return rows

# Paste-code cloud OAuth setup
def add_cloud_account(options):
    client_id = options["client_id"] or input("Sonos client id: ").strip()
    client_secret = options["client_secret"] or input("Sonos client secret: ").strip()
    api_key = options["key"] or input("Sonos API key: ").strip()
    if not client_id or not client_secret or not api_key:
        return "Sonos client id, client secret, and API key are required."
    auth_url = build_sonos_auth_url(client_id, REDIRECT_URI_PASTE)
    print("Open this URL, sign in, then paste the code:")
    print(auth_url)
    code = options["code"] or input("Code: ").strip()
    if not code:
        return "Authorization code is required."
    tokens = accounts_store.exchange_authorization_code("sonos", code, client_id, client_secret, REDIRECT_URI_PASTE)
    account = {
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
    accounts_store.upsert_account(account, load=options["accounts_path"], save=options["accounts_path"])
    return f"Saved Sonos cloud account {options['account_id']} to {options['accounts_path']}."

# Build the Sonos authorize URL
def build_sonos_auth_url(client_id, redirect_uri):
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "state": "speak",
        "scope": SONOS_SCOPE,
        "redirect_uri": redirect_uri,
    })
    return SONOS_AUTH_URL + "?" + query

# Main
if __name__ == "__main__":
    main()
