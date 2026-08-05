#!/usr/bin/env python3

# Imports
import json
import os
import re
import sys
import time
import subprocess
import urllib.error
import urllib.request

import dates
import maths
import memory
import reminders

# Config
API_URL = "http://127.0.0.1:8080/v1/chat/completions"
API_KEY = "local"
MODEL = "gemma-4-e2b"
DEFAULT_PROMPT = "Introduce yourself in one short sentence."
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
PROMPT_PATH = os.path.join(SPEAK_DIR, "prompt.json")
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 100
TEMPERATURE = 0.7
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 40
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 60
VOLUME_CONTROLS = ("Speaker", "PCM", "Master")
VOLUME_RETRY_PROMPT = "Do not guess. Call set_volume now with the requested percent, then answer using only the tool result."
VOICE_RETRY_PROMPT = "Do not guess. Call set_voice with the requested voice name now, then answer using only the tool result."

# Tools the local model can call
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": "Turn the robot head left, right, or center.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "center"],
                        "description": "Which way to face.",
                    },
                    "degrees": {
                        "type": "number",
                        "description": f"How far to turn for left or right, default {LOOK_DEFAULT_DEGREES}, max 90.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the speaker volume to a percent from 0 to 100.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "number",
                        "description": "Volume percent from 0 to 100.",
                    },
                },
                "required": ["percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Get the current speaker volume percent.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
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
] + dates.TOOLS + maths.TOOLS + memory.TOOLS + reminders.TOOLS

# Conversation history kept across asks in this process
conversation_history = []
talk_module = None
last_volume_percent = None
last_tool_log = []

# Main
def main():
    # Parse prompt
    prompt = parse_args()

    # Ask local model
    response = ask_model(prompt)
    print(response)

# Parse prompt from command line
def parse_args():
    # Use supplied text or a useful default
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])
    return DEFAULT_PROMPT

# Ask the model, running any tool calls it requests
def ask_model(prompt):
    # Start a fresh tool log for this ask
    last_tool_log.clear()

    # Answer volume follow-ups from the last actual ALSA value
    if last_volume_percent is not None and re.search(r"\bwhat did you set\b", prompt.lower()) and "voice" not in prompt.lower():
        reply = f"I set the volume to {last_volume_percent} percent."
        remember_exchange(prompt, reply)
        return reply

    # Answer day counts in Python with the next matching future date
    if dates.needs_date_math(prompt):
        reply = dates.answer_date_math(prompt, record_tool, conversation_history, trim_conversation_history)
        if reply:
            return reply

    # Answer which year the last date calculation used
    if dates.needs_prior_calculate_year(prompt):
        reply = dates.answer_prior_calculate_year(prompt, conversation_history, remember_exchange, parse_tool_arguments)
        if reply:
            return reply

    # Answer with the last day-count number from history
    if dates.needs_prior_calculate_result(prompt):
        reply = dates.answer_prior_calculate_result(prompt, conversation_history, remember_exchange)
        if reply:
            return reply

    # Start from the system prompt with long-term memories, prior turns, and this question
    messages = [{"role": "system", "content": system_prompt_with_memories()}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": prompt})
    math_retry_used = False
    math_tool_used = False
    date_math_retry_used = False
    last_calculate_expression = None
    last_calculate_result = None
    volume_retry_used = False
    volume_set_used = False
    volume_get_used = False
    voice_retry_used = False
    voice_set_used = False
    memory_retry_used = False
    memory_remember_used = False
    memory_forget_used = False
    reminder_retry_used = False
    reminder_set_used = False
    reminder_cancel_used = False
    reminder_list_used = False
    last_list_voices_result = None
    last_list_reminders_result = None

    # Loop until the model replies with spoken text
    for _ in range(MAX_TOOL_ROUNDS):
        message = chat_completion(messages)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            reply = (message.get("content") or "").strip()

            # Force or apply set_reminder when the model skipped scheduling
            if reminders.needs_set_reminder(prompt) and not reminder_set_used:
                forced = reminders.force_set_reminder(prompt, messages, message, reminder_retry_used, record_tool)
                if forced is True:
                    reminder_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force or apply cancel_reminder when the model skipped canceling
            if reminders.needs_cancel_reminder(prompt) and not reminder_cancel_used:
                forced = reminders.force_cancel_reminder(prompt, messages, message, reminder_retry_used, record_tool)
                if forced is True:
                    reminder_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Prefer the exact list_reminders tool text when listing
            if reminders.needs_list_reminders(prompt) and not reminders.needs_set_reminder(prompt) and not reminders.needs_cancel_reminder(prompt):
                if last_list_reminders_result:
                    remember_turn(messages, last_list_reminders_result)
                    return last_list_reminders_result
                if not reminder_list_used:
                    forced = reminders.force_list_reminders(prompt, messages, message, reminder_retry_used, record_tool)
                    if forced is True:
                        reminder_retry_used = True
                        continue
                    if forced:
                        remember_turn(messages, forced)
                        return forced

            # Force or apply remember when the model skipped saving a fact
            if memory.needs_remember(prompt) and not reminders.needs_set_reminder(prompt) and not memory_remember_used:
                forced = memory.force_remember(prompt, messages, message, memory_retry_used, record_tool)
                if forced is True:
                    memory_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force or apply forget when the model skipped dropping a fact
            if memory.needs_forget(prompt) and not reminders.needs_cancel_reminder(prompt) and not memory_forget_used:
                forced = memory.force_forget(prompt, messages, message, memory_retry_used, record_tool)
                if forced is True:
                    memory_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Prefer a date expression already calculated, else force Python date math
            if dates.needs_date_math(prompt):
                spoken = dates.speak_model_date_calculate(last_calculate_expression, last_calculate_result)
                if spoken:
                    remember_turn(messages, spoken)
                    return spoken
                forced = dates.force_date_calculate(prompt, messages, message, date_math_retry_used, record_tool)
                if forced is True:
                    date_math_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force calculate when the model guessed arithmetic
            if maths.needs_math_tool(prompt) and not dates.needs_date_math(prompt) and not math_tool_used and not math_retry_used:
                math_retry_used = True
                print("[ask] missing math tool, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": maths.MATH_RETRY_PROMPT})
                continue

            # Force or apply set_volume when the model skipped a volume change
            if needs_set_volume(prompt) and not volume_set_used:
                forced = force_set_volume(prompt, messages, message, volume_retry_used)
                if forced is True:
                    volume_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force get_volume when the model guessed the current volume
            if needs_get_volume(prompt) and not volume_get_used and not volume_retry_used:
                volume_retry_used = True
                print("[ask] missing get_volume, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": "Do not guess. Call get_volume now, then answer using only the tool result."})
                continue

            # Force or apply set_voice when the model skipped a voice change
            if needs_set_voice(prompt) and not voice_set_used:
                forced = force_set_voice(prompt, messages, message, voice_retry_used)
                if forced is True:
                    voice_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Prefer the exact list_voices tool text when listing
            if needs_list_voices(prompt) and not needs_set_voice(prompt):
                if last_list_voices_result:
                    remember_turn(messages, last_list_voices_result)
                    return last_list_voices_result
                arguments = list_voices_arguments(prompt)
                result = run_list_voices(arguments)
                record_tool("list_voices", arguments, result)
                remember_turn(messages, result)
                return result

            # Prefer a clear spoken confirmation after a successful volume set
            if not reply and volume_set_used:
                if last_volume_percent is not None:
                    reply = f"I have set the volume to {last_volume_percent} percent."
            remember_turn(messages, reply or "Okay.")
            return reply or "Okay."

        # Keep the assistant tool call turn, then return each tool result
        print(f"[ask] tools: {[call.get('function', {}).get('name') for call in tool_calls]}", flush=True)
        messages.append(message)
        for tool_call in tool_calls:
            inject_list_voices_count(tool_call, prompt)
            result = run_tool(tool_call)
            tool_name = (tool_call.get("function") or {}).get("name")
            if tool_name == "calculate":
                math_tool_used = True
                arguments = parse_tool_arguments((tool_call.get("function") or {}).get("arguments"))
                last_calculate_expression = arguments.get("expression", "")
                last_calculate_result = result
            if tool_name == "set_volume":
                volume_set_used = True
            if tool_name == "get_volume":
                volume_get_used = True
            if tool_name == "set_voice":
                voice_set_used = True
            if tool_name == "list_voices":
                last_list_voices_result = result
            if tool_name == "remember":
                memory_remember_used = True
            if tool_name == "forget":
                memory_forget_used = True
            if tool_name == "set_reminder":
                reminder_set_used = True
            if tool_name == "cancel_reminder":
                reminder_cancel_used = True
            if tool_name == "list_reminders":
                reminder_list_used = True
                last_list_reminders_result = result
            messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result})

        # Resolve day counts in Python, or keep a valid date expression the model already ran
        if dates.needs_date_math(prompt):
            forced = dates.force_date_calculate(prompt, messages, None, True, record_tool)
            if forced:
                remember_turn(messages, forced)
                return forced
            spoken = dates.speak_model_date_calculate(last_calculate_expression, last_calculate_result)
            if spoken:
                remember_turn(messages, spoken)
                return spoken

        # If it talked about reminders but skipped the tool, apply it in Python now
        if reminders.needs_set_reminder(prompt) and not reminder_set_used:
            forced = reminders.force_set_reminder(prompt, messages, None, True, record_tool)
            if forced:
                remember_turn(messages, forced)
                return forced
        if reminders.needs_cancel_reminder(prompt) and not reminder_cancel_used:
            forced = reminders.force_cancel_reminder(prompt, messages, None, True, record_tool)
            if forced:
                remember_turn(messages, forced)
                return forced
        if reminders.needs_list_reminders(prompt) and last_list_reminders_result and not reminders.needs_set_reminder(prompt) and not reminders.needs_cancel_reminder(prompt):
            remember_turn(messages, last_list_reminders_result)
            return last_list_reminders_result

        # If it talked about remembering but did not call remember, save it in Python now
        if memory.needs_remember(prompt) and not reminders.needs_set_reminder(prompt) and not memory_remember_used:
            forced = memory.force_remember(prompt, messages, None, True, record_tool)
            if forced:
                remember_turn(messages, forced)
                return forced

        # If it talked about forgetting but did not call forget, drop it in Python now
        if memory.needs_forget(prompt) and not reminders.needs_cancel_reminder(prompt) and not memory_forget_used:
            forced = memory.force_forget(prompt, messages, None, True, record_tool)
            if forced:
                remember_turn(messages, forced)
                return forced

        # If it checked volume instead of setting it, set it in Python now
        if needs_set_volume(prompt) and not volume_set_used:
            forced = force_set_volume(prompt, messages, None, True)
            if forced is True:
                volume_set_used = True
                continue
            if forced:
                remember_turn(messages, forced)
                return forced

        # If it listed voices instead of setting one, set it in Python now
        if needs_set_voice(prompt) and not voice_set_used:
            forced = force_set_voice(prompt, messages, None, True)
            if forced is True:
                voice_set_used = True
                continue
            if forced:
                remember_turn(messages, forced)
                return forced

        # Speak the exact voice list from the tool, do not let the model shorten it
        if needs_list_voices(prompt) and last_list_voices_result and not needs_set_voice(prompt):
            remember_turn(messages, last_list_voices_result)
            return last_list_voices_result

    # Give up after too many tool rounds
    reply = "I could not finish that request."
    remember_turn(messages, reply)
    return reply

# Return true when the question needs a volume tool
def needs_volume_tool(prompt):
    return needs_set_volume(prompt) or needs_get_volume(prompt)

# Return true when the user wants the volume changed
def needs_set_volume(prompt):
    text = prompt.lower()
    if re.search(r"\b(louder|quieter|mute|unmute)\b", text):
        return True
    if "volume" not in text:
        return False
    if re.search(r"\d+\s*%", text):
        return True
    return bool(re.search(r"\b(set|change|make|turn|raise|lower)\b", text))

# Return true when the user only wants the current volume
def needs_get_volume(prompt):
    text = prompt.lower()
    if re.search(r"\bwhat did you set\b", text) and "voice" not in text:
        return True
    if "volume" not in text:
        return False
    if needs_set_volume(prompt):
        return False
    return bool(re.search(r"\b(what|how|current|check|get|tell)\b", text))

# Read a volume percent from the user text when present
def parse_volume_percent(prompt):
    text = prompt.lower()
    if re.search(r"\bmute\b", text) and not re.search(r"\bunmute\b", text):
        return 0
    match = re.search(r"(\d+)\s*%", text)
    if match:
        return max(0, min(100, int(match.group(1))))
    match = re.search(r"\b(?:to|at)\s+(\d+)\b", text)
    if match:
        return max(0, min(100, int(match.group(1))))
    return None

# Retry set_volume once, then apply it in Python if still missing
def force_set_volume(prompt, messages, message, already_retried):
    percent = parse_volume_percent(prompt)

    # First miss, ask the model again with an explicit set_volume order
    if not already_retried:
        print("[ask] missing set_volume, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if percent is None:
            retry = VOLUME_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call set_volume with percent {percent} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, set it directly in Python
    if percent is None:
        return None
    arguments = {"percent": percent}
    result = run_set_volume(arguments)
    record_tool("set_volume", arguments, result)
    print(f"[ask] forced set_volume -> {result}", flush=True)
    if last_volume_percent is not None:
        return f"I have set the volume to {last_volume_percent} percent."
    return f"I have set the volume to {percent} percent."

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

# Read a voice name from the user text when present
def parse_voice_name(prompt):
    talk = load_talk_module()
    text = prompt.lower()

    # Prefer a known full or short voice name mentioned in the text
    if talk is not None:
        voices = getattr(talk, "VOICES", [])
        matches = []
        for voice in voices:
            short = talk.voice_short_name(voice)
            for name in (voice, short, voice.replace("_", " "), short.replace("_", " ")):
                if re.search(rf"\b{re.escape(name)}\b", text):
                    matches.append((len(name), voice))
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

# Retry set_voice once, then apply it in Python if still missing
def force_set_voice(prompt, messages, message, already_retried):
    voice = parse_voice_name(prompt)

    # First miss, ask the model again with an explicit set_voice order
    if not already_retried:
        print("[ask] missing set_voice, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if voice is None:
            retry = VOICE_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call set_voice with voice {voice} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, set it directly in Python
    if voice is None:
        return None
    arguments = {"voice": voice}
    result = run_set_voice(arguments)
    record_tool("set_voice", arguments, result)
    print(f"[ask] forced set_voice -> {result}", flush=True)
    return result

# Remember one question and spoken reply when tools were not used
def remember_exchange(prompt, reply):
    conversation_history.append({"role": "user", "content": prompt})
    conversation_history.append({"role": "assistant", "content": reply})
    trim_conversation_history()

# Keep this ask's messages, including tool calls and results, for later asks
def remember_turn(messages, reply):
    global conversation_history
    conversation_history = list(messages[1:])
    conversation_history.append({"role": "assistant", "content": reply})
    trim_conversation_history()

# Drop oldest full turns when history grows too long
def trim_conversation_history():
    while len(conversation_history) > MAX_HISTORY_MESSAGES:
        conversation_history.pop(0)
        while conversation_history and conversation_history[0].get("role") != "user":
            conversation_history.pop(0)

# Load the system prompt from speak/prompt.json
def load_system_prompt():
    with open(PROMPT_PATH, encoding="utf-8") as prompt_file:
        data = json.load(prompt_file)
    system = data.get("system")
    if isinstance(system, list):
        prompt = "\n\n".join(str(part).strip() for part in system if str(part).strip())
    else:
        prompt = str(system or "").strip()
    if not prompt:
        raise ValueError(f"Missing system prompt in {PROMPT_PATH}")
    return prompt

# System prompt plus long-term memories and reminders when any exist
def system_prompt_with_memories():
    prompt = load_system_prompt()
    extras = []
    memory_text = memory.format_memories_for_prompt()
    if memory_text:
        extras.append(memory_text)
    reminder_text = reminders.format_reminders_for_prompt()
    if reminder_text:
        extras.append(reminder_text)
    if not extras:
        return prompt
    return prompt + "\n\n" + "\n\n".join(extras)

# Send one chat request to llama-server
def chat_completion(messages):
    # Build request body
    body = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    # Send request
    request = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as result:
        response = json.load(result)

    # Return the assistant message
    return response["choices"][0]["message"]

# Run one tool call and return a short result string
def run_tool(tool_call):
    # Read the function name and arguments
    function = tool_call.get("function") or {}
    name = function.get("name", "")
    arguments = parse_tool_arguments(function.get("arguments"))

    # Turn the head through robot look.py
    if name == "look":
        result = run_look(arguments)
        record_tool(name, arguments, result)
        return result

    # Return the current clock time, date, or weekday
    if name == "get_time":
        result = dates.clock_time()
        record_tool(name, arguments, result)
        return result
    if name == "get_date":
        result = dates.calendar_date()
        record_tool(name, arguments, result)
        return result
    if name == "get_day":
        result = dates.calendar_day()
        record_tool(name, arguments, result)
        return result

    # Evaluate math in Python
    if name == "calculate":
        result = maths.run_calculate(arguments.get("expression", ""))
        record_tool(name, arguments, result)
        return result

    # Set the speaker volume
    if name == "set_volume":
        result = run_set_volume(arguments)
        record_tool(name, arguments, result)
        return result

    # Read the speaker volume
    if name == "get_volume":
        result = run_get_volume()
        record_tool(name, arguments, result)
        return result

    # Change the speaking voice
    if name == "set_voice":
        result = run_set_voice(arguments)
        record_tool(name, arguments, result)
        return result

    # List speaking voices
    if name == "list_voices":
        result = run_list_voices(arguments)
        record_tool(name, arguments, result)
        return result

    # Save or drop a long-term memory fact
    if name == "remember":
        result = memory.run_remember(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "forget":
        result = memory.run_forget(arguments)
        record_tool(name, arguments, result)
        return result

    # Schedule, cancel, or list daily spoken reminders
    if name == "set_reminder":
        result = reminders.run_set_reminder(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "cancel_reminder":
        result = reminders.run_cancel_reminder(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "list_reminders":
        result = reminders.run_list_reminders(arguments)
        record_tool(name, arguments, result)
        return result

    # Unknown tool
    result = f"Unknown tool: {name}"
    record_tool(name, arguments, result)
    return result

# Print a tool call and keep it for the talks log
def record_tool(name, arguments, result):
    call_line = f"[tool] {name} {arguments}"
    result_line = f"[tool] {name} -> {result}"
    last_tool_log.append(call_line)
    last_tool_log.append(result_line)
    print(call_line, flush=True)
    print(result_line, flush=True)

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

# Call robot look.py to turn the head
def run_look(arguments):
    # Import look from the robot source tree
    look = load_robot_look()
    if look is None:
        return "Look is unavailable."

    # Read direction and optional degrees
    direction = arguments.get("direction", "left")
    degrees = arguments.get("degrees", LOOK_DEFAULT_DEGREES)
    try:
        return look.look(direction, degrees)
    except Exception as error:
        return f"Look failed: {error}"

# Import ~/robot/src/look.py once
def load_robot_look():
    if "look" in sys.modules:
        return sys.modules["look"]
    if not os.path.isdir(ROBOT_SRC):
        return None
    if ROBOT_SRC not in sys.path:
        sys.path.insert(0, ROBOT_SRC)
    try:
        import look
    except ImportError:
        return None
    return look

# Set speaker volume from tool arguments
def run_set_volume(arguments):
    global last_volume_percent
    if "percent" not in arguments:
        return "Volume percent is required."
    try:
        percent = int(round(float(arguments["percent"])))
    except (TypeError, ValueError):
        return "Volume percent must be a number."
    percent = max(0, min(100, percent))
    if not set_speaker_volume(percent):
        return "Could not set the volume."
    actual = read_speaker_volume()
    if actual is None:
        last_volume_percent = percent
        return f"Volume set to {percent} percent."
    last_volume_percent = actual
    return f"Volume set to {actual} percent."

# Read speaker volume for the tool
def run_get_volume():
    global last_volume_percent
    percent = read_speaker_volume()
    if percent is None:
        return "Could not read the volume."
    last_volume_percent = percent
    return f"Volume is {percent} percent."

# Change the talk speaking voice
def run_set_voice(arguments):
    talk = load_talk_module()
    if talk is None:
        return "Voice control is unavailable."
    voice = arguments.get("voice", "")
    try:
        return talk.set_voice(voice)
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

# Import talk.py once for voice control
def load_talk_module():
    if talk_module is not None:
        return talk_module
    if "talk" in sys.modules:
        return sys.modules["talk"]
    if "__main__" in sys.modules and getattr(sys.modules["__main__"], "set_voice", None):
        return sys.modules["__main__"]
    talk_path = os.path.join(os.path.dirname(SCRIPT_DIR), "talk.py")
    if not os.path.isfile(talk_path):
        return None
    speak_dir = os.path.dirname(SCRIPT_DIR)
    if speak_dir not in sys.path:
        sys.path.insert(0, speak_dir)
    try:
        import talk
    except ImportError:
        return None
    return talk

# Let talk.py register itself when running as __main__
def set_talk_module(module):
    global talk_module
    talk_module = module

# Set the USB speaker volume with amixer
def set_speaker_volume(percent):
    card = find_volume_card()
    control = find_volume_control(card)
    if card is None or control is None:
        return False
    result = subprocess.run(["amixer", "-c", str(card), "set", control, f"{percent}%", "unmute"], capture_output=True, text=True)
    return result.returncode == 0

# Read the current USB speaker volume percent
def read_speaker_volume():
    card = find_volume_card()
    control = find_volume_control(card)
    if card is None or control is None:
        return None
    result = subprocess.run(["amixer", "-c", str(card), "get", control], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    match = re.search(r"\[(\d+)%\]", result.stdout)
    if not match:
        return None
    return int(match.group(1))

# Find the first playback volume control on a card
def find_volume_control(card):
    if card is None:
        return None
    for control in VOLUME_CONTROLS:
        result = subprocess.run(["amixer", "-c", str(card), "get", control], capture_output=True, text=True)
        if result.returncode == 0 and re.search(r"\[\d+%\]", result.stdout):
            return control
    return None

# Find the USB playback card index used for speech
def find_volume_card():
    cards_path = "/proc/asound/cards"
    if not os.path.isfile(cards_path):
        return 0 if os.path.exists("/proc/asound/card0") else None

    # Collect USB card indexes
    usb_cards = []
    with open(cards_path) as cards_file:
        for line in cards_file:
            if "USB-Audio" not in line:
                continue
            card_index_text = line.strip().split(None, 1)[0]
            if card_index_text.isdigit():
                usb_cards.append(int(card_index_text))

    # Prefer the speaker-only card, one without a mic
    for card_index in usb_cards:
        if not volume_card_has_capture(card_index):
            return card_index
    if usb_cards:
        return usb_cards[0]
    return 0 if os.path.exists("/proc/asound/card0") else None

# Return true when a card has a capture stream
def volume_card_has_capture(card_index):
    stream_path = f"/proc/asound/card{card_index}/stream0"
    if not os.path.isfile(stream_path):
        return False
    with open(stream_path) as stream_file:
        return "Capture:" in stream_file.read()

# Main
if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        print(f"LLM unavailable at {API_URL}: {error.reason}")
        sys.exit(1)
