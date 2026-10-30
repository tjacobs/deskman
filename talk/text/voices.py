#!/usr/bin/env python3

# Speaking voice control through talk.py

# Imports
import json
import os
import re
import sys

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
VOICE_RETRY_PROMPT = "Do not guess. Call set_voice with the requested voice name now, then answer using only the tool result."

# Tools the local model can call for voice
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_voice",
            "description": "Change the speaking voice. Use a Kokoro voice id like bm_fable or af_heart, or a short name like fable or heart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "voice": {
                        "type": "string",
                        "description": "Voice id or short name, for example bm_fable, af_bella, or george.",
                    },
                },
                "required": ["voice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_voices",
            "description": "List speaking voice names without af am bf bm prefixes. Use count when the user asks for a number of voices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "number",
                        "description": "Optional number of voices to list from the start of the list.",
                    },
                },
            },
        },
    },
]

# State
talk_module = None

# Main
def main():
    # List voices by default
    print(run_list_voices())

# Return true when the user wants the speaking voice changed
def needs_set_voice(prompt):
    text = prompt.lower()
    if re.search(r"\b(did you|have you)\b", text):
        return False
    if needs_list_voices(prompt):
        return False
    if re.search(r"\bvoice\s+to\b", text):
        return True
    return bool(re.search(r"\b(change|set|switch|use)\b.*\bvoice\b|\bvoice\b.*\b(to|change|set|switch)\b", text))

# Return true when the user wants the voice list spoken
def needs_list_voices(prompt):
    text = prompt.lower()
    if re.search(r"\b(list|show)\b.*\bvoices?\b", text):
        return True
    if re.search(r"\bwhat voices\b|\bwhich voices\b", text):
        return True
    return bool(re.search(r"\bvoices?\b.*\b(list|available|have)\b", text))

# Retry set_voice once, then apply it in Python if still missing
def force_set_voice(prompt, messages, message, already_retried, record_tool):
    voice_name = parse_voice_name(prompt)

    # First miss, ask the model again with an explicit set_voice order
    if not already_retried:
        print("[voice] missing set_voice, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if voice_name is None:
            retry = VOICE_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call set_voice with voice {voice_name} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, set it directly in Python
    if voice_name is None:
        return None
    arguments = {"voice": voice_name}
    result = run_set_voice(arguments)
    record_tool("set_voice", arguments, result)
    print(f"[voice] forced set_voice -> {result}", flush=True)
    return result

# Change the talk speaking voice
def run_set_voice(arguments):
    talk = load_talk_module()
    if talk is None:
        return "Voice control is unavailable."
    voice_name = arguments.get("voice", "")
    try:
        return talk.set_voice(voice_name)
    except Exception as error:
        return f"Voice change failed: {error}"

# List talk speaking voices
def run_list_voices(arguments=None):
    talk = load_talk_module()
    if talk is None:
        return "Voice list is unavailable."
    arguments = arguments or {}
    count = arguments.get("count")
    try:
        if count is None:
            return talk.list_voices()
        return talk.list_voices(count)
    except Exception as error:
        return f"Voice list failed: {error}"

# Build list_voices arguments from the user text
def list_voices_arguments(prompt):
    count = parse_voice_count(prompt)
    if count is None:
        return {}
    return {"count": count}

# Fill in list_voices count when the model omitted it
def inject_list_voices_count(tool_call, prompt):
    function = tool_call.get("function") or {}
    if function.get("name") != "list_voices":
        return
    arguments = parse_tool_arguments(function.get("arguments"))
    if "count" in arguments:
        return
    count = parse_voice_count(prompt)
    if count is None:
        return
    function["arguments"] = json.dumps({**arguments, "count": count})

# Read a voice name from the user text when present
def parse_voice_name(prompt):
    talk = load_talk_module()
    text = prompt.lower()

    # Prefer a known full or short voice name mentioned in the text
    if talk is not None:
        voices = getattr(talk, "VOICES", [])
        matches = []
        for voice_name in voices:
            short = talk.voice_short_name(voice_name)
            for name in (voice_name, short, voice_name.replace("_", " "), short.replace("_", " ")):
                if re.search(rf"\b{re.escape(name)}\b", text):
                    matches.append((len(name), voice_name))
        if matches:
            matches.sort(reverse=True)
            return matches[0][1]

    # Fall back to the words after voice to
    match = re.search(r"\bvoice\s+to\s+(.+?)(?:[.!?]|$)", text)
    if match:
        return match.group(1).strip(" ,")
    return None

# Read how many voices to list when the user asked for a count
def parse_voice_count(prompt):
    match = re.search(r"\b(\d+)\s+voices?\b", prompt.lower())
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"\blist\s+(\d+)\b", prompt.lower())
    if match:
        return max(1, int(match.group(1)))
    return None

# Parse tool arguments from JSON text or a dict
def parse_tool_arguments(raw_arguments):
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not raw_arguments:
        return {}
    try:
        return json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}

# Import talk.py once for voice control
def load_talk_module():
    if talk_module is not None:
        return talk_module
    if "talk" in sys.modules:
        return sys.modules["talk"]
    if "__main__" in sys.modules and getattr(sys.modules["__main__"], "set_voice", None):
        return sys.modules["__main__"]
    talk_path = os.path.join(SPEAK_DIR, "talk.py")
    if not os.path.isfile(talk_path):
        return None
    if SPEAK_DIR not in sys.path:
        sys.path.insert(0, SPEAK_DIR)
    try:
        import talk
    except ImportError:
        return None
    return talk

# Let talk.py register itself when running as __main__
def set_talk_module(module):
    global talk_module
    talk_module = module

# Main
if __name__ == "__main__":
    main()
