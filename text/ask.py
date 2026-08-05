#!/usr/bin/env python3

# Imports
import ast
import calendar
import json
import math
import os
import re
import sys
import time
import subprocess
import urllib.error
import urllib.request
from datetime import date, datetime

import reminders

# Config
API_URL = "http://127.0.0.1:8080/v1/chat/completions"
API_KEY = "local"
MODEL = "gemma-4-e2b"
DEFAULT_PROMPT = "Introduce yourself in one short sentence."
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
PROMPT_PATH = os.path.join(SPEAK_DIR, "prompt.json")
MEMORY_PATH = os.path.join(SPEAK_DIR, "memory.json")
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 100
TEMPERATURE = 0.7
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 40
MAX_MEMORY_FACTS = 50
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 60
VOLUME_CONTROLS = ("Speaker", "PCM", "Master")
MATH_RETRY_PROMPT = "Do not guess. Call calculate with a Python math expression now, then answer using only the tool result."
DATE_MATH_RETRY_PROMPT = "Do not guess. Call calculate with a date expression using date and today, for example (date(2026, 8, 15) - today()).days, then answer using only the tool result. Never pass a bare number."
VOLUME_RETRY_PROMPT = "Do not guess. Call set_volume now with the requested percent, then answer using only the tool result."
VOICE_RETRY_PROMPT = "Do not guess. Call set_voice with the requested voice name now, then answer using only the tool result."
MEMORY_RETRY_PROMPT = "Do not guess. Call remember with the fact the user asked you to keep, then answer using only the tool result."
FORGET_RETRY_PROMPT = "Do not guess. Call forget with the fact the user asked you to drop, then answer using only the tool result."
MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
MONTH_LABELS = ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
DATE_ATTRS = ("days", "year", "month", "day")
MATH_SAFE_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)

MATH_ENV = {
    "math": math,
    "date": date,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
}

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
            "name": "get_time",
            "description": "Get the current local clock time. Use only for the current time of day, not for remembered or scheduled times like dinner.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": "Get today's local calendar date. Use only for the current date, not for other named dates.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_day",
            "description": "Get today's day of the week. Use only for the current weekday.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a Python math or date expression. Required for arithmetic and day counts. For days until a date use (date(year, month, day) - today()).days. Never invent the answer and never pass a bare number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Python expression, for example 17 * 43, math.sqrt(144), or (date(2026, 8, 15) - today()).days.",
                    },
                },
                "required": ["expression"],
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
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save a long-term fact the user asked you to remember. Survives reboot. Required when the user says remember.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The fact to remember, for example Dinner time is 6:30 PM.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": "Remove a long-term remembered fact. Required when the user says forget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text matching the fact to forget.",
                    },
                    "id": {
                        "type": "number",
                        "description": "Optional fact id to forget.",
                    },
                },
            },
        },
    },
] + reminders.TOOLS

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
    if needs_date_math(prompt):
        reply = answer_date_math(prompt)
        if reply:
            return reply

    # Answer which year the last date calculation used
    if needs_prior_calculate_year(prompt):
        reply = answer_prior_calculate_year(prompt)
        if reply:
            return reply

    # Answer with the last day-count number from history
    if needs_prior_calculate_result(prompt):
        reply = answer_prior_calculate_result(prompt)
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
            if needs_remember(prompt) and not memory_remember_used:
                forced = force_remember(prompt, messages, message, memory_retry_used)
                if forced is True:
                    memory_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force or apply forget when the model skipped dropping a fact
            if needs_forget(prompt) and not memory_forget_used:
                forced = force_forget(prompt, messages, message, memory_retry_used)
                if forced is True:
                    memory_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Prefer a date expression already calculated, else force Python date math
            if needs_date_math(prompt):
                spoken = speak_model_date_calculate(last_calculate_expression, last_calculate_result)
                if spoken:
                    remember_turn(messages, spoken)
                    return spoken
                forced = force_date_calculate(prompt, messages, message, date_math_retry_used)
                if forced is True:
                    date_math_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force calculate when the model guessed arithmetic
            if needs_math_tool(prompt) and not math_tool_used and not math_retry_used:
                math_retry_used = True
                print("[ask] missing math tool, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": MATH_RETRY_PROMPT})
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
        if needs_date_math(prompt):
            forced = force_date_calculate(prompt, messages, None, True)
            if forced:
                remember_turn(messages, forced)
                return forced
            spoken = speak_model_date_calculate(last_calculate_expression, last_calculate_result)
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
        if needs_remember(prompt) and not memory_remember_used:
            forced = force_remember(prompt, messages, None, True)
            if forced:
                remember_turn(messages, forced)
                return forced

        # If it talked about forgetting but did not call forget, drop it in Python now
        if needs_forget(prompt) and not memory_forget_used:
            forced = force_forget(prompt, messages, None, True)
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

# Return true when the user asked to save a long-term fact
def needs_remember(prompt):
    text = prompt.lower()
    if needs_forget(prompt) or reminders.needs_set_reminder(prompt):
        return False
    return bool(re.search(r"\bremember\b", text))

# Return true when the user asked to drop a long-term fact
def needs_forget(prompt):
    if reminders.needs_cancel_reminder(prompt):
        return False
    return bool(re.search(r"\bforget\b", prompt.lower()))

# Return true when the question needs a day-count date calculation
def needs_date_math(prompt):
    text = prompt.lower()
    if re.search(r"\bhow many days\b", text):
        return True
    if re.search(r"\bdays?\s+(until|till|to|left|remaining)\b", text):
        return True
    if re.search(r"\b(until|till)\b.*\b(" + "|".join(MONTH_NAMES) + r")\b", text):
        return True
    return bool(re.search(r"\b(end of|left in)\s+(the\s+)?(month|year)\b|\bnew year'?s?\b", text))

# Return true when the question needs the Python math tool
def needs_math_tool(prompt):
    text = prompt.lower()
    if "volume" in text or needs_date_math(prompt):
        return False
    if "square root" in text:
        return True
    if re.search(r"\b(plus|minus|times|divided|multiply|calculate)\b", text):
        return True
    if re.search(r"\d+\s*percent\s+of\b", text):
        return True
    if re.search(r"\d+\s*[\+\-\*\/x×÷]\s*\d+", text):
        return True
    return bool(re.search(r"\b(what is|what's)\s+\d", text))

# Return true when the user asks which year a prior calculation used
def needs_prior_calculate_year(prompt):
    text = prompt.lower()
    return bool(re.search(r"\b(which|what)\s+year\b", text))

# Return true when the user asks for the last calculated day count
def needs_prior_calculate_result(prompt):
    text = prompt.lower()
    if needs_date_math(prompt):
        return False
    if re.search(r"\b(tell me|what was|what's|what is)\s+(the\s+)?number\b", text):
        return True
    if re.search(r"\bthe number\b", text) and re.search(r"\b(history|calculation|calculated)\b", text):
        return True
    return bool(re.search(r"\b(read|check)\b.*\b(history|calculation|context)\b", text))

# Answer day counts with a Python date expression and keep it in history
def answer_date_math(prompt):
    expression, target = date_math_expression(prompt)
    if expression is None:
        return None
    result = run_calculate(expression)
    record_tool("calculate", {"expression": expression}, result)
    reply = speak_date_math_result(result, target)
    remember_date_math_turn(prompt, expression, result, reply)
    return reply

# Answer which year the last calculate date expression used
def answer_prior_calculate_year(prompt):
    expression = last_history_calculate_expression()
    if not expression:
        return None
    match = re.search(r"\bdate\s*\(\s*(\d{4})", expression)
    if not match:
        return None
    reply = f"I used the year {match.group(1)}."
    remember_exchange(prompt, reply)
    return reply

# Answer with the last calculate day-count result from history
def answer_prior_calculate_result(prompt):
    result = last_history_calculate_result()
    if result is None:
        return None
    reply = f"The number is {result}."
    remember_exchange(prompt, reply)
    return reply

# Keep a date-math tool call and spoken reply in conversation history
def remember_date_math_turn(prompt, expression, result, reply):
    tool_call_id = "date_math"
    conversation_history.append({"role": "user", "content": prompt})
    conversation_history.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": "calculate",
                "arguments": json.dumps({"expression": expression}),
            },
        }],
    })
    conversation_history.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
    conversation_history.append({"role": "assistant", "content": reply})
    trim_conversation_history()

# Read the most recent calculate expression from history
def last_history_calculate_expression():
    for message in reversed(conversation_history):
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function") or {}
            if function.get("name") != "calculate":
                continue
            arguments = parse_tool_arguments(function.get("arguments"))
            expression = arguments.get("expression")
            if expression:
                return expression
    return None

# Read the most recent calculate tool result from history
def last_history_calculate_result():
    for index in range(len(conversation_history) - 1, -1, -1):
        message = conversation_history[index]
        if message.get("role") != "tool":
            continue
        tool_call_id = message.get("tool_call_id")
        for prior_index in range(index - 1, -1, -1):
            prior = conversation_history[prior_index]
            for tool_call in prior.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                if tool_call.get("id") == tool_call_id and function.get("name") == "calculate":
                    content = message.get("content")
                    if content is not None and str(content).strip() != "":
                        return str(content).strip()
            if prior.get("role") == "user":
                break
    return None

# Build a date subtraction expression for the user question
def date_math_expression(prompt):
    target = parse_target_date(prompt)
    if target is not None:
        year, month, day = target
        return f"(date({year}, {month}, {day}) - today()).days", target
    text = prompt.lower()
    today_value = date.today()
    if re.search(r"\bend of (the )?year\b|\bnew year'?s?\b|\bleft in (the )?year\b", text):
        target = (today_value.year, 12, 31)
        return f"(date({target[0]}, 12, 31) - today()).days", target
    if re.search(r"\bdays?\s+left|\bremaining days\b|\bend of (the )?month\b|\bleft in (the )?month\b", text):
        last_day = calendar.monthrange(today_value.year, today_value.month)[1]
        target = (today_value.year, today_value.month, last_day)
        return f"(date({target[0]}, {target[1]}, {target[2]}) - today()).days", target
    return None, None

# Speak a day count from a date expression the model already calculated
def speak_model_date_calculate(expression, result):
    if result is None or not expression:
        return None
    if not re.search(r"\btoday\s*\(\s*\)", expression):
        return None
    if not re.search(r"\.days\b", expression):
        return None
    target = parse_date_expression_target(expression)
    if target is None:
        return None
    return speak_date_math_result(result, target)

# Read year month day from a date(...) call in an expression
def parse_date_expression_target(expression):
    match = re.search(r"\bdate\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)", expression or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))

# Read a month day and optional year from the user text
def parse_target_date(prompt):
    text = prompt.lower()
    month_pattern = "|".join(sorted(MONTH_NAMES, key=len, reverse=True))
    match = re.search(rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?\b", text)
    if match:
        month = MONTH_NAMES[match.group(1)]
        day = int(match.group(2))
        year_text = match.group(3)
    else:
        match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_pattern})(?:\s*,?\s*(\d{{4}}))?\b", text)
        if not match:
            return None
        day = int(match.group(1))
        month = MONTH_NAMES[match.group(2)]
        year_text = match.group(3)
    if day < 1 or day > 31:
        return None
    today_value = date.today()
    if year_text:
        year = int(year_text)
    else:
        year = today_value.year
        if date(year, month, min(day, calendar.monthrange(year, month)[1])) < today_value:
            year += 1
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        return None
    return year, month, day

# Retry date math once, then compute it in Python if still missing
def force_date_calculate(prompt, messages, message, already_retried):
    expression, target = date_math_expression(prompt)

    # First miss, ask the model again with an explicit date expression
    if not already_retried:
        print("[ask] missing date math, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if expression is None:
            retry = DATE_MATH_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call calculate with expression {expression} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, calculate it directly in Python
    if expression is None:
        return None
    result = run_calculate(expression)
    record_tool("calculate", {"expression": expression}, result)
    print(f"[ask] forced date calculate -> {result}", flush=True)
    return speak_date_math_result(result, target)

# Turn a day-count tool result into a short spoken reply
def speak_date_math_result(result, target):
    try:
        days = int(result)
    except (TypeError, ValueError):
        return f"I could not finish that date calculation."
    if target is None:
        return f"There are {days} days."
    year, month, day = target
    label = f"{MONTH_LABELS[month]} {day}, {year}"
    if days < 0:
        return f"{label} was {abs(days)} days ago."
    if days == 0:
        return f"Today is {label}."
    if days == 1:
        return f"There is 1 day until {label}."
    return f"There are {days} days until {label}."

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
    memory_text = format_memories_for_prompt()
    if memory_text:
        extras.append(memory_text)
    reminder_text = reminders.format_reminders_for_prompt()
    if reminder_text:
        extras.append(reminder_text)
    if not extras:
        return prompt
    return prompt + "\n\n" + "\n\n".join(extras)

# Format saved facts for the system prompt
def format_memories_for_prompt():
    facts = load_memory_facts()
    if not facts:
        return ""
    lines = [f"- {fact.get('text', '').strip()}" for fact in facts if str(fact.get("text") or "").strip()]
    if not lines:
        return ""
    return "Long-term memories:\n" + "\n".join(lines)

# Load remembered facts from disk
def load_memory_facts():
    if not os.path.isfile(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH, encoding="utf-8") as memory_file:
            data = json.load(memory_file)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    facts = data.get("facts")
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict) and str(fact.get("text") or "").strip()]

# Save remembered facts to disk atomically
def save_memory_facts(facts):
    payload = {"facts": facts}
    temporary_path = MEMORY_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as memory_file:
        json.dump(payload, memory_file, indent=2)
        memory_file.write("\n")
    os.replace(temporary_path, MEMORY_PATH)

# Next numeric id for a new memory fact
def next_memory_id(facts):
    if not facts:
        return 1
    return max(int(fact.get("id") or 0) for fact in facts) + 1

# Save one long-term fact from tool arguments
def run_remember(arguments):
    text = str(arguments.get("text") or "").strip()
    if not text:
        return "Remember text is required."
    facts = load_memory_facts()

    # Replace an existing similar fact, or append a new one
    lowered = text.lower()
    for fact in facts:
        if str(fact.get("text") or "").strip().lower() == lowered:
            fact["text"] = text
            fact["saved_at"] = datetime.now().astimezone().isoformat()
            save_memory_facts(facts)
            return f"I have remembered that {text}"
    facts.append({"id": next_memory_id(facts), "text": text, "saved_at": datetime.now().astimezone().isoformat()})

    # Drop the oldest facts when over the cap
    while len(facts) > MAX_MEMORY_FACTS:
        facts.pop(0)
    save_memory_facts(facts)
    return f"I have remembered that {text}"

# Remove matching long-term facts from tool arguments
def run_forget(arguments):
    facts = load_memory_facts()
    if not facts:
        return "I do not have any long-term memories saved."
    fact_id = arguments.get("id")
    text = str(arguments.get("text") or "").strip().lower()
    kept = []
    removed = []
    for fact in facts:
        fact_text = str(fact.get("text") or "").strip()
        if fact_id is not None and int(fact.get("id") or -1) == int(fact_id):
            removed.append(fact_text)
            continue
        if text and text in fact_text.lower():
            removed.append(fact_text)
            continue
        kept.append(fact)
    if not removed:
        return "I could not find that memory to forget."
    save_memory_facts(kept)
    if len(removed) == 1:
        return f"I have forgotten that {removed[0]}"
    return f"I have forgotten {len(removed)} memories."

# Read the fact to remember from the user text
def parse_remember_text(prompt):
    text = prompt.strip()
    text = re.sub(r"[,.]?\s*(okay[,.]?\s*)?remember\s+(it|that|this)\s*[.!]?\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*remember\s+(that|this)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(okay[,.]?\s*)?(please\s+)?(i'?m gonna tell you[,.]?\s*)?", "", text, flags=re.IGNORECASE)
    text = text.strip(" ,.!")
    if not text:
        return None
    return text[0].upper() + text[1:]

# Read what to forget from the user text
def parse_forget_text(prompt):
    text = prompt.strip()
    match = re.search(r"\bforget\s+(?:that\s+|about\s+)?(.+?)\s*[.!]?\s*$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" ,.!")
    return None

# Retry remember once, then save it in Python if still missing
def force_remember(prompt, messages, message, already_retried):
    text = parse_remember_text(prompt)

    # First miss, ask the model again with an explicit remember order
    if not already_retried:
        print("[ask] missing remember, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if text is None:
            retry = MEMORY_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call remember with text {text} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, save it directly in Python
    if text is None:
        return None
    arguments = {"text": text}
    result = run_remember(arguments)
    record_tool("remember", arguments, result)
    print(f"[ask] forced remember -> {result}", flush=True)
    return result

# Retry forget once, then drop it in Python if still missing
def force_forget(prompt, messages, message, already_retried):
    text = parse_forget_text(prompt)

    # First miss, ask the model again with an explicit forget order
    if not already_retried:
        print("[ask] missing forget, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if text is None:
            retry = FORGET_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call forget with text {text} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, forget it directly in Python
    if text is None:
        return None
    arguments = {"text": text}
    result = run_forget(arguments)
    record_tool("forget", arguments, result)
    print(f"[ask] forced forget -> {result}", flush=True)
    return result

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

    # Return the current clock time
    if name == "get_time":
        result = clock_time()
        record_tool(name, arguments, result)
        return result

    # Return the current calendar date
    if name == "get_date":
        result = calendar_date()
        record_tool(name, arguments, result)
        return result

    # Return the current weekday
    if name == "get_day":
        result = calendar_day()
        record_tool(name, arguments, result)
        return result

    # Evaluate math in Python
    if name == "calculate":
        result = run_calculate(arguments.get("expression", ""))
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

    # Save a long-term memory fact
    if name == "remember":
        result = run_remember(arguments)
        record_tool(name, arguments, result)
        return result

    # Drop a long-term memory fact
    if name == "forget":
        result = run_forget(arguments)
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

# Return today's date for calculate expressions
def math_today():
    return date.today()

MATH_ENV["today"] = math_today

# Evaluate a safe Python math expression
def run_calculate(expression):
    expression = str(expression or "").strip()
    if not expression:
        return "Math error: empty expression."

    # Parse and reject anything outside basic math
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        return f"Math error: {error.msg}"
    if not math_expression_safe(tree):
        return "Math error: expression not allowed."

    # Evaluate with a tiny math-only environment
    try:
        result = eval(compile(tree, "<math>", "eval"), {"__builtins__": {}}, MATH_ENV)
    except Exception as error:
        return f"Math error: {error}"
    return format_math_result(result)

# Return true when the expression tree is only basic math or dates
def math_expression_safe(tree):
    for node in ast.walk(tree):
        if not isinstance(node, MATH_SAFE_NODES):
            return False
        if isinstance(node, ast.Name) and node.id not in MATH_ENV:
            return False
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "math":
                continue
            if node.attr in DATE_ATTRS:
                continue
            return False
    return True

# Format a math result for a short spoken reply
def format_math_result(result):
    if isinstance(result, bool):
        return str(result)
    if isinstance(result, int):
        return str(result)
    if isinstance(result, float):
        if result.is_integer():
            return str(int(result))
        return f"{result:.10g}"
    if isinstance(result, date):
        return f"{MONTH_LABELS[result.month]} {result.day}, {result.year}"
    return str(result)

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

# Format the time without a leading zero, speech reads it as a number
def clock_time():
    now = time.localtime()
    hour = now.tm_hour % 12 or 12
    return f"{hour}:{now.tm_min:02d} {time.strftime('%p', now)}"

# Format the date with weekday, month, day, and year
def calendar_date():
    now = time.localtime()
    return f"{time.strftime('%A, %B', now)} {now.tm_mday}, {now.tm_year}"

# Format the weekday name
def calendar_day():
    return time.strftime("%A", time.localtime())

# Main
if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        print(f"LLM unavailable at {API_URL}: {error.reason}")
        sys.exit(1)
