#!/usr/bin/env python3

# Guided Sonos LAN setup that writes accounts.json

# Imports
import os
import subprocess
import sys

import accounts as accounts_store
import accounts.sonos as sonos_account

# Config defaults
DEFAULT_ACCOUNT_ID = sonos_account.DEFAULT_LOCAL_ACCOUNT_ID
DEFAULT_LABEL = sonos_account.DEFAULT_LOCAL_LABEL

# Main
def main():
    # Parse flags and run the guided or non-interactive LAN setup
    options = parse_args()
    print(run_lan_setup(options))

# Parse guided LAN setup flags
def parse_args():
    options = {
        "account_id": DEFAULT_ACCOUNT_ID,
        "label": DEFAULT_LABEL,
        "default_speaker": "",
        "speakers": [],
        "accounts_path": accounts_store.ACCOUNTS_PATH,
        "smoke": None,
    }
    words = sys.argv[1:]
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
        if word == "--default-speaker" and index + 1 < len(words):
            options["default_speaker"] = words[index + 1]
            index += 2
            continue
        if word == "--speaker" and index + 1 < len(words):
            options["speakers"].append(sonos_account.parse_speaker_flag(words[index + 1]))
            index += 2
            continue
        if word == "--accounts" and index + 1 < len(words):
            options["accounts_path"] = os.path.expanduser(words[index + 1])
            index += 2
            continue
        if word == "--smoke":
            options["smoke"] = True
            index += 1
            continue
        if word == "--no-smoke":
            options["smoke"] = False
            index += 1
            continue
        if word in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        print(f"Unknown argument: {word}")
        print_usage()
        sys.exit(2)
    return options

# Print usage help
def print_usage():
    print("Usage: python3 -m accounts.auth_sonos_lan [flags...]")
    print("  Guided Sonos LAN discovery and accounts.json write")
    print("  Run on the Pi or any machine on the same WiFi as the speakers")
    print("  --id --label --speaker Name=IP --default-speaker --accounts --smoke --no-smoke")

# Run interactive or flagged LAN setup
def run_lan_setup(options):
    print("Sonos LAN setup")
    print("Use the same WiFi as your Sonos speakers.")
    ensure_soco_installed()
    if not options["speakers"]:
        speakers = discover_or_enter_speakers()
    else:
        speakers = options["speakers"]
    if not speakers:
        return "No Sonos speakers to save."
    speakers = choose_speakers(speakers, options["speakers"])
    default_speaker = options["default_speaker"] or choose_default_speaker(speakers)
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
    lines = [f"Saved Sonos LAN account {options['account_id']} to {options['accounts_path']}.", accounts_store.format_accounts_summary(accounts_store.load_accounts(options["accounts_path"]), load=options["accounts_path"])]
    if should_smoke(options):
        lines.append(run_smoke_test(account, default_speaker))
    return "\n".join(lines)

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
    print("Installing soco with: uv pip install soco")
    result = subprocess.run(["uv", "pip", "install", "--python", python_path, "soco"], check=False)
    if result.returncode != 0:
        print("Could not install soco. Run: uv pip install soco")
        return
    try:
        import soco
    except ImportError:
        print("soco installed, but this process still cannot import it. Re-run ./auth_sonos.py.")
        sys.exit(1)

# Discover speakers, or fall back to manual IP entry
def discover_or_enter_speakers():
    print("Discovering Sonos speakers on the LAN...")
    try:
        speakers = sonos_account.discover_local_speakers()
    except ImportError as error:
        print(str(error))
        speakers = []
    if speakers:
        return speakers
    print("No speakers found. Check WiFi, VLANs, and firewall multicast, then enter speakers manually.")
    return enter_speakers_manually()

# Prompt for name and IP until a blank name
def enter_speakers_manually():
    speakers = []
    while True:
        name = input("Speaker name, blank to finish: ").strip()
        if not name:
            break
        ip_address = input(f"IP for {name}: ").strip()
        if not ip_address:
            print("IP is required.")
            continue
        speakers.append({"name": name, "ip": ip_address})
    return speakers

# Print a numbered speaker table
def print_speaker_table(speakers):
    print("   Name                 IP")
    for index, speaker in enumerate(speakers, start=1):
        print(f"{index:<3}{speaker['name']:<21}{speaker['ip']}")

# Let the user keep all or a subset when interactive
def choose_speakers(speakers, provided):
    if provided:
        return speakers
    print_speaker_table(speakers)
    choice = input("Keep which numbers, Enter for all: ").strip()
    if not choice:
        return speakers
    kept = []
    for part in choice.replace(",", " ").split():
        index = int(part) - 1
        if 0 <= index < len(speakers):
            kept.append(speakers[index])
    return kept or speakers

# Ask which speaker is the default
def choose_default_speaker(speakers):
    print_speaker_table(speakers)
    choice = input(f"Default speaker number or name [{speakers[0]['name']}]: ").strip()
    if not choice:
        return speakers[0]["name"]
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(speakers):
            return speakers[index]["name"]
    for speaker in speakers:
        if speaker["name"].lower() == choice.lower():
            return speaker["name"]
    return speakers[0]["name"]

# Decide whether to smoke-test volume
def should_smoke(options):
    if options["smoke"] is True:
        return True
    if options["smoke"] is False:
        return False
    if options["speakers"]:
        return False
    answer = input("Smoke-test volume on the default speaker? [y/N]: ").strip().lower()
    return answer in ("y", "yes")

# Read volume on the default speaker as a smoke test
def run_smoke_test(account, default_speaker):
    try:
        device = sonos_account.local_device(account, default_speaker)
        return f"Smoke test ok: {device.player_name} volume is {device.volume}."
    except (ValueError, OSError, ImportError) as error:
        return f"Smoke test failed: {error}"

# Main
if __name__ == "__main__":
    main()
