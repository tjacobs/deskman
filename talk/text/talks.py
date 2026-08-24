#!/usr/bin/env python3

# Load a day's talk log from talks/YYYY-MM-DD.txt into the model context

# Imports
import os
import re
from datetime import date, timedelta

import dates

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
TALKS_DIR = os.path.join(SPEAK_DIR, "talks")
MAX_TALK_LOG_CHARS = 6000
TALK_LOG_RETRY_PROMPT = "Do not guess. Call load_talk_log with the requested day now, then answer using only the tool result."

# Tools the local model can call for talk logs
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "load_talk_log",
            "description": "Load one day's talk conversation log into context. Use for yesterday's talk, a date's conversation, or what was said that day. Date may be today, yesterday, or YYYY-MM-DD.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Day to load, for example today, yesterday, or 2026-08-05.",
                    },
                },
            },
        },
    },
]

# Main
def main():
    # Load today's talk log by default
    print(run_load_talk_log({}))

# Return true when the user asked to load a day's talk log
def needs_load_talk_log(prompt):
    text = prompt.lower()
    if re.search(r"\b(talk|conversation|chat)\s+log\b", text):
        return True
    if re.search(r"\b(yesterday'?s?|prior|previous)\s+(talk|conversation|chat|history)\b", text):
        return True
    if re.search(r"\b(load|read|show|check|access)\b.*\b(talk|conversation|chat)\b", text):
        return True
    if re.search(r"\bwhat (did|have) we (say|said|talk|talked)\b", text) and re.search(r"\b(yesterday|\d{4}-\d{2}-\d{2})\b", text):
        return True
    return bool(re.search(r"\byesterday'?s?\s+(memories|conversation|talk)\b", text))

# Retry load_talk_log once, then load it in Python if still missing
def force_load_talk_log(prompt, messages, message, already_retried, record_tool):
    day = parse_talk_day(prompt)

    # First miss, ask the model again with an explicit load order
    if not already_retried:
        print("[talks] missing load_talk_log, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if day is None:
            retry = TALK_LOG_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call load_talk_log with date {day.isoformat()} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, load it directly in Python
    arguments = {}
    if day is not None:
        arguments["date"] = day.isoformat()
    result = run_load_talk_log(arguments)
    record_tool("load_talk_log", arguments, result)
    print(f"[talks] forced load_talk_log -> {result[:120]}...", flush=True)
    return result

# Load one day's talk log from disk
def run_load_talk_log(arguments=None):
    arguments = arguments or {}
    day = parse_talk_day(str(arguments.get("date") or "today"))
    if day is None:
        day = date.today()
    path = os.path.join(TALKS_DIR, day.isoformat() + ".txt")
    if not os.path.isfile(path):
        return f"No talk log found for {day.isoformat()}."

    # Read the log and keep the newest part when over the size cap
    with open(path, encoding="utf-8") as talk_file:
        text = talk_file.read().strip()
    if not text:
        return f"The talk log for {day.isoformat()} is empty."
    if len(text) > MAX_TALK_LOG_CHARS:
        text = "...\n" + text[-MAX_TALK_LOG_CHARS:]
    return f"Talk log for {day.isoformat()}:\n{text}"

# Parse today, yesterday, YYYY-MM-DD, or a month day into a date
def parse_talk_day(text):
    raw = str(text or "").strip().lower()
    if not raw or raw in ("today", "now"):
        return date.today()
    if raw == "yesterday":
        return date.today() - timedelta(days=1)
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if re.search(r"\byesterday\b", raw):
        return date.today() - timedelta(days=1)
    if re.search(r"\btoday\b", raw):
        return date.today()
    target = dates.parse_target_date(raw)
    if target is None:
        return None
    return date(target[0], target[1], target[2])

# Main
if __name__ == "__main__":
    main()
