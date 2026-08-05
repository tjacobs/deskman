#!/usr/bin/env python3

# Daily spoken reminders stored in reminders.json

# Imports
import json
import os
import re
import threading
import time
from datetime import date

import memory

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
REMINDERS_PATH = os.path.join(SPEAK_DIR, "reminders.json")
MAX_REMINDERS = 20
REMINDER_RETRY_PROMPT = "Do not guess. Call set_reminder with the name and time now, then answer using only the tool result."
CANCEL_REMINDER_RETRY_PROMPT = "Do not guess. Call cancel_reminder with the reminder name now, then answer using only the tool result."
LIST_REMINDER_RETRY_PROMPT = "Do not guess. Call list_reminders now, then answer using only the tool result."

# Tools the local model can call for reminders
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Schedule a daily spoken reminder at a clock time. Required when the user asks to be reminded.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short reminder name, for example dinner or bedtime.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Clock time like 6:30 PM, or a named time like dinner time from memory.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Optional spoken line, for example It's dinner time.",
                    },
                },
                "required": ["name", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Cancel a daily spoken reminder by name. Required when the user cancels a reminder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Reminder name to cancel, for example dinner or bedtime.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List daily spoken reminders. Required when the user asks what reminders are set.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

# State
reminders_lock = threading.RLock()

# Main
def main():
    # List reminders by default
    print(run_list_reminders())

# Create dinner and bedtime reminders from memory when missing
def seed_reminders_from_memory():
    with reminders_lock:
        reminders = load_reminders_unlocked()
        names = {reminder.get("name") for reminder in reminders}
        added = False
        for name in ("dinner", "bedtime"):
            if name in names:
                continue
            resolved = None
            for fact in memory.load_memory_facts():
                text = str(fact.get("text") or "")
                lowered = text.lower()
                if name == "dinner" and "dinner" not in lowered:
                    continue
                if name == "bedtime" and "bed" not in lowered:
                    continue
                resolved = parse_clock_time(text)
                if resolved is not None:
                    break
            if resolved is None:
                continue
            hour, minute = resolved
            reminders.append({
                "id": next_reminder_id(reminders),
                "name": name,
                "hour": hour,
                "minute": minute,
                "message": default_reminder_message(name),
                "last_fired_date": None,
            })
            added = True
            print(f"[reminders] seeded {name} at {format_clock_time(hour, minute)}", flush=True)
        if added:
            save_reminders_unlocked(reminders)

# Return due reminders for this minute and mark them fired for today
def pop_due_reminders():
    now = time.localtime()
    today = date.today().isoformat()
    due = []
    with reminders_lock:
        reminders = load_reminders_unlocked()
        changed = False
        for reminder in reminders:
            if int(reminder.get("hour")) != now.tm_hour or int(reminder.get("minute")) != now.tm_min:
                continue
            if reminder.get("last_fired_date") == today:
                continue
            reminder["last_fired_date"] = today
            due.append({"name": reminder.get("name"), "message": reminder.get("message") or default_reminder_message(reminder.get("name"))})
            changed = True
        if changed:
            save_reminders_unlocked(reminders)
    return due

# Format daily reminders for the system prompt
def format_reminders_for_prompt():
    reminders = load_reminders()
    if not reminders:
        return ""
    lines = []
    for reminder in reminders:
        name = str(reminder.get("name") or "").strip()
        message = str(reminder.get("message") or "").strip()
        clock = format_clock_time(int(reminder.get("hour") or 0), int(reminder.get("minute") or 0))
        lines.append(f"- {name} at {clock}: {message}")
    return "Daily reminders:\n" + "\n".join(lines)

# Return true when the user asked to schedule a daily reminder
def needs_set_reminder(prompt):
    text = prompt.lower()
    if needs_cancel_reminder(prompt) or needs_list_reminders(prompt):
        return False
    if re.search(r"\bremind(?:\s+me)?\b", text):
        return True
    return bool(re.search(r"\b(set|add|create)\b.*\breminder\b", text))

# Return true when the user asked to cancel a daily reminder
def needs_cancel_reminder(prompt):
    text = prompt.lower()
    if re.search(r"\bstop reminding\b", text):
        return True
    if re.search(r"\b(cancel|stop|delete|remove)\b.*\breminder", text):
        return True
    return bool(re.search(r"\breminder\b.*\b(cancel|stop|delete|remove)\b", text))

# Return true when the user asked what reminders are set
def needs_list_reminders(prompt):
    text = prompt.lower()
    if needs_cancel_reminder(prompt) or re.search(r"\bremind(?:\s+me)?\b", text):
        return False
    if re.search(r"\b(list|what|which|show)\b.*\breminders?\b", text):
        return True
    return bool(re.search(r"\breminders?\b.*\b(have|set|on|active)\b", text))

# Retry set_reminder once, then schedule it in Python if still missing
def force_set_reminder(prompt, messages, message, already_retried, record_tool):
    arguments = parse_set_reminder(prompt)

    # First miss, ask the model again with an explicit reminder order
    if not already_retried:
        print("[reminders] missing set_reminder, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if arguments is None:
            retry = REMINDER_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call set_reminder with name {arguments['name']} and time {arguments['time'] or arguments['name']} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, schedule it directly in Python
    if arguments is None:
        return None
    result = run_set_reminder(arguments)
    record_tool("set_reminder", arguments, result)
    print(f"[reminders] forced set_reminder -> {result}", flush=True)
    return result

# Retry cancel_reminder once, then cancel it in Python if still missing
def force_cancel_reminder(prompt, messages, message, already_retried, record_tool):
    name = parse_cancel_reminder_name(prompt)

    # First miss, ask the model again with an explicit cancel order
    if not already_retried:
        print("[reminders] missing cancel_reminder, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if name is None:
            retry = CANCEL_REMINDER_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call cancel_reminder with name {name} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, cancel it directly in Python
    if name is None:
        return None
    arguments = {"name": name}
    result = run_cancel_reminder(arguments)
    record_tool("cancel_reminder", arguments, result)
    print(f"[reminders] forced cancel_reminder -> {result}", flush=True)
    return result

# Retry list_reminders once, then list them in Python if still missing
def force_list_reminders(prompt, messages, message, already_retried, record_tool):
    # First miss, ask the model again with an explicit list order
    if not already_retried:
        print("[reminders] missing list_reminders, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": LIST_REMINDER_RETRY_PROMPT})
        return True

    # Second miss, list them directly in Python
    result = run_list_reminders()
    record_tool("list_reminders", {}, result)
    print(f"[reminders] forced list_reminders -> {result}", flush=True)
    return result

# Schedule or update a daily reminder from tool arguments
def run_set_reminder(arguments):
    name = normalize_reminder_name(arguments.get("name"))
    if not name:
        return "Reminder name is required."
    time_text = str(arguments.get("time") or "").strip()
    parsed = parse_clock_time(time_text) if time_text else None
    if parsed is None:
        parsed = resolve_named_time(time_text or name)
    if parsed is None:
        parsed = resolve_named_time(name)
    if parsed is None:
        return "Reminder time is required, like 6:30 PM."
    hour, minute = parsed
    message = str(arguments.get("message") or "").strip() or default_reminder_message(name)
    with reminders_lock:
        reminders = load_reminders_unlocked()
        for reminder in reminders:
            if reminder.get("name") == name:
                reminder["hour"] = hour
                reminder["minute"] = minute
                reminder["message"] = message
                reminder["last_fired_date"] = None
                save_reminders_unlocked(reminders)
                return f"I will remind you about {name} every day at {format_clock_time(hour, minute)}."
        reminders.append({
            "id": next_reminder_id(reminders),
            "name": name,
            "hour": hour,
            "minute": minute,
            "message": message,
            "last_fired_date": None,
        })
        while len(reminders) > MAX_REMINDERS:
            reminders.pop(0)
        save_reminders_unlocked(reminders)
    return f"I will remind you about {name} every day at {format_clock_time(hour, minute)}."

# Cancel a daily reminder from tool arguments
def run_cancel_reminder(arguments):
    name = normalize_reminder_name(arguments.get("name"))
    if not name:
        return "Reminder name is required."
    with reminders_lock:
        reminders = load_reminders_unlocked()
        kept = [reminder for reminder in reminders if reminder.get("name") != name]
        if len(kept) == len(reminders):
            return f"I could not find a {name} reminder."
        save_reminders_unlocked(kept)
    return f"I canceled the {name} reminder."

# List daily reminders for a short spoken reply
def run_list_reminders(arguments=None):
    reminders = load_reminders()
    if not reminders:
        return "You have no daily reminders set."
    parts = [f"{reminder['name']} at {format_clock_time(reminder['hour'], reminder['minute'])}" for reminder in reminders]
    if len(parts) == 1:
        return f"You have {parts[0]}."
    return "You have " + ", ".join(parts[:-1]) + f", and {parts[-1]}."

# Read reminder name and time from the user text
def parse_set_reminder(prompt):
    text = prompt.strip()
    lowered = text.lower()
    name = None
    if re.search(r"\bdinner\b", lowered):
        name = "dinner"
    elif re.search(r"\bbed\s*time\b|\bbedtime\b", lowered):
        name = "bedtime"
    else:
        match = re.search(r"\bremind(?:\s+me)?\s+(?:about|for|to)\s+(.+?)(?:\s+at\s+|\s*[.!?]|$)", lowered)
        if match:
            name = normalize_reminder_name(match.group(1))
        if name is None:
            match = re.search(r"\b(?:set|add|create)\s+(?:a\s+|an\s+)?(.+?)\s+reminder\b", lowered)
            if match:
                name = normalize_reminder_name(match.group(1))
    time_match = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b", text, flags=re.IGNORECASE)
    time_text = time_match.group(1).strip() if time_match else ""
    if name is None and time_text:
        name = "reminder"
    if name is None:
        return None
    if not time_text:
        time_text = name
    return {"name": name, "time": time_text, "message": default_reminder_message(name)}

# Read which reminder to cancel from the user text
def parse_cancel_reminder_name(prompt):
    lowered = prompt.lower()
    if re.search(r"\bdinner\b", lowered):
        return "dinner"
    if re.search(r"\bbed\s*time\b|\bbedtime\b", lowered):
        return "bedtime"
    match = re.search(r"\b(?:cancel|stop|delete|remove)\s+(?:the\s+)?(.+?)\s+reminder\b", lowered)
    if match:
        return normalize_reminder_name(match.group(1))
    match = re.search(r"\breminder\s+(?:for|about)\s+(.+?)\s*[.!]?\s*$", lowered)
    if match:
        return normalize_reminder_name(match.group(1))
    return None

# Resolve a named time from memory facts or existing reminders
def resolve_named_time(name):
    key = normalize_reminder_name(name)
    if not key:
        return None
    for reminder in load_reminders():
        if reminder.get("name") == key:
            return int(reminder["hour"]), int(reminder["minute"])
    for fact in memory.load_memory_facts():
        text = str(fact.get("text") or "")
        if key not in text.lower() and key.replace("time", "").strip() not in text.lower():
            if key == "bedtime" and "bed" not in text.lower():
                continue
            if key != "bedtime":
                continue
        parsed = parse_clock_time(text)
        if parsed is not None:
            return parsed
    return parse_clock_time(name)

# Load reminders from disk
def load_reminders():
    with reminders_lock:
        return load_reminders_unlocked()

# Load reminders without taking the lock
def load_reminders_unlocked():
    if not os.path.isfile(REMINDERS_PATH):
        return []
    try:
        with open(REMINDERS_PATH, encoding="utf-8") as reminders_file:
            data = json.load(reminders_file)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    reminders = data.get("reminders")
    if not isinstance(reminders, list):
        return []
    cleaned = []
    for reminder in reminders:
        if not isinstance(reminder, dict):
            continue
        name = normalize_reminder_name(reminder.get("name"))
        try:
            hour = int(reminder.get("hour"))
            minute = int(reminder.get("minute"))
        except (TypeError, ValueError):
            continue
        if not name or hour < 0 or hour > 23 or minute < 0 or minute > 59:
            continue
        cleaned.append({
            "id": int(reminder.get("id") or 0),
            "name": name,
            "hour": hour,
            "minute": minute,
            "message": str(reminder.get("message") or default_reminder_message(name)).strip(),
            "last_fired_date": reminder.get("last_fired_date"),
        })
    return cleaned

# Save reminders to disk atomically without taking the lock
def save_reminders_unlocked(reminders):
    payload = {"reminders": reminders}
    temporary_path = REMINDERS_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as reminders_file:
        json.dump(payload, reminders_file, indent=2)
        reminders_file.write("\n")
    os.replace(temporary_path, REMINDERS_PATH)

# Next numeric id for a new reminder
def next_reminder_id(reminders):
    if not reminders:
        return 1
    return max(int(reminder.get("id") or 0) for reminder in reminders) + 1

# Normalize a reminder name to a short key
def normalize_reminder_name(name):
    text = re.sub(r"\s+", " ", str(name or "").strip().lower())
    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = re.sub(r"\s+reminder$", "", text)
    if text in ("bed time", "bed", "sleep"):
        return "bedtime"
    if text in ("dinner time", "supper"):
        return "dinner"
    return text

# Default spoken line for a reminder name
def default_reminder_message(name):
    if name == "dinner":
        return "It's dinner time"
    if name == "bedtime":
        return "It's bed time"
    return f"It's time for {name}"

# Parse a clock time from free text into hour and minute
def parse_clock_time(text):
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").lower().replace(".", "")
    if minute > 59:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        if ampm.startswith("p") and hour != 12:
            hour += 12
        if ampm.startswith("a") and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return hour, minute

# Format hour and minute for a short spoken clock time
def format_clock_time(hour, minute):
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"

# Main
if __name__ == "__main__":
    main()
