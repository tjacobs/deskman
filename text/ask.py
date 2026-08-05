#!/usr/bin/env python3

# Imports
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Start the clock here, the ask modules load next so their cost is counted
STARTUP_START = time.perf_counter()

# Import ask modules
import dates
import maths
import memory
import move
import reminders
import voice
import volume

# Record how long the ask modules took to import
IMPORT_SECONDS = time.perf_counter() - STARTUP_START

# Config
API_URL = "http://127.0.0.1:8080/v1/chat/completions"
API_KEY = "local"
MODEL = "gemma-4-e2b"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
PROMPT_PATH = os.path.join(SPEAK_DIR, "prompt.json")
SERVER_SCRIPT = os.path.join(SCRIPT_DIR, "server.sh")
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 100
TEMPERATURE = 0.7
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 40

# Tools the local model can call
TOOLS = move.TOOLS + dates.TOOLS + maths.TOOLS + memory.TOOLS + reminders.TOOLS + voice.TOOLS + volume.TOOLS

# Conversation history kept across asks in this process
conversation_history = []
last_tool_log = []

# Timing for the last ask, only printed when run standalone
show_timing = False
last_round_timings = []
last_prompt_seconds = 0
last_completion_tokens = 0

# Main
def main():
    # Parse prompt
    prompt = parse_args()

    # Print timing when run standalone, talk.py keeps its own output clean
    global show_timing
    show_timing = True

    # Ask local model
    ask_start = time.perf_counter()
    response = ask_model(prompt)
    print(response)

    # Print where the time went
    print_timing(ask_start)

# Parse prompt from command line
def parse_args():
    # Use supplied text or a useful default
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])
    return "Say hello."

# Ask the model, running any tool calls it requests
def ask_model(prompt):
    # Start a fresh tool log and timing record for this ask
    last_tool_log.clear()
    last_round_timings.clear()

    # Answer volume follow-ups from the last actual ALSA value
    reply = volume.answer_last_volume_set(prompt)
    if reply:
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
    global last_prompt_seconds
    prompt_start = time.perf_counter()
    system_message = system_prompt_with_memories()
    last_prompt_seconds = time.perf_counter() - prompt_start
    messages = [{"role": "system", "content": system_message}]
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
        # Time the model call and start a new row in the timing table
        model_start = time.perf_counter()
        message = chat_completion(messages)
        record_round(time.perf_counter() - model_start, last_completion_tokens)
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
            if volume.needs_set_volume(prompt) and not volume_set_used:
                forced = volume.force_set_volume(prompt, messages, message, volume_retry_used, record_tool)
                if forced is True:
                    volume_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Force get_volume when the model guessed the current volume
            if volume.needs_get_volume(prompt) and not volume_get_used and not volume_retry_used:
                volume_retry_used = True
                print("[ask] missing get_volume, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": volume.GET_VOLUME_RETRY_PROMPT})
                continue

            # Force or apply set_voice when the model skipped a voice change
            if voice.needs_set_voice(prompt) and not voice_set_used:
                forced = voice.force_set_voice(prompt, messages, message, voice_retry_used, record_tool)
                if forced is True:
                    voice_retry_used = True
                    continue
                if forced:
                    remember_turn(messages, forced)
                    return forced

            # Prefer the exact list_voices tool text when listing
            if voice.needs_list_voices(prompt) and not voice.needs_set_voice(prompt):
                if last_list_voices_result:
                    remember_turn(messages, last_list_voices_result)
                    return last_list_voices_result
                arguments = voice.list_voices_arguments(prompt)
                result = voice.run_list_voices(arguments)
                record_tool("list_voices", arguments, result)
                remember_turn(messages, result)
                return result

            # Prefer a clear spoken confirmation after a successful volume set
            if not reply and volume_set_used:
                reply = volume.confirm_volume_set() or reply
            remember_turn(messages, reply or "Okay.")
            return reply or "Okay."

        # Keep the assistant tool call turn, then return each tool result
        print(f"[ask] tools: {[call.get('function', {}).get('name') for call in tool_calls]}", flush=True)
        messages.append(message)
        tools_start = time.perf_counter()
        for tool_call in tool_calls:
            voice.inject_list_voices_count(tool_call, prompt)
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

        # Add the tool time to the row for this round
        record_round_tools(time.perf_counter() - tools_start)

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
        if volume.needs_set_volume(prompt) and not volume_set_used:
            forced = volume.force_set_volume(prompt, messages, None, True, record_tool)
            if forced is True:
                volume_set_used = True
                continue
            if forced:
                remember_turn(messages, forced)
                return forced

        # If it listed voices instead of setting one, set it in Python now
        if voice.needs_set_voice(prompt) and not voice_set_used:
            forced = voice.force_set_voice(prompt, messages, None, True, record_tool)
            if forced is True:
                voice_set_used = True
                continue
            if forced:
                remember_turn(messages, forced)
                return forced

        # Speak the exact voice list from the tool, do not let the model shorten it
        if voice.needs_list_voices(prompt) and last_list_voices_result and not voice.needs_set_voice(prompt):
            remember_turn(messages, last_list_voices_result)
            return last_list_voices_result

    # Give up after too many tool rounds
    reply = "I could not finish that request."
    remember_turn(messages, reply)
    return reply

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
    global last_completion_tokens

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

    # Keep the generated token count so the timing table can show tokens per second
    usage = response.get("usage") or {}
    last_completion_tokens = int(usage.get("completion_tokens") or 0)

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
        result = move.run_look(arguments)
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

    # Set or read the speaker volume
    if name == "set_volume":
        result = volume.run_set_volume(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "get_volume":
        result = volume.run_get_volume(arguments)
        record_tool(name, arguments, result)
        return result

    # Change or list speaking voices
    if name == "set_voice":
        result = voice.run_set_voice(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "list_voices":
        result = voice.run_list_voices(arguments)
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

# Record one model round for the timing table
def record_round(model_seconds, tokens):
    last_round_timings.append({"model": model_seconds, "tokens": tokens, "tools": 0})

# Add the tool time to the round just recorded
def record_round_tools(tools_seconds):
    if last_round_timings:
        last_round_timings[-1]["tools"] = tools_seconds

# Print where this ask spent its time
def print_timing(ask_start):
    if not show_timing:
        return

    # Show startup costs paid before the model was called
    log_elapsed("Import modules", IMPORT_SECONDS)
    log_elapsed("Load prompt", last_prompt_seconds)

    # Show one row per model round, some asks are answered in Python with no rounds
    if last_round_timings:
        print_round_header()
        for index, round_timing in enumerate(last_round_timings, start=1):
            log_round_timing(index, round_timing)

    # Show totals
    log_timing("Ask total", ask_start)
    log_timing("Script total", STARTUP_START)

# Print the round timing header
def print_round_header():
    print(f"{'Round':>5}  {'Model':>8}  {'Tokens':>7}  {'Speed':>9}  {'Tools':>8}")

# Print one round with model time, tokens generated, and tool time
def log_round_timing(index, round_timing):
    model_seconds = round_timing["model"]
    tokens = round_timing["tokens"]
    tools_seconds = round_timing["tools"]
    speed = tokens / model_seconds if model_seconds > 0 else 0
    print(f"{index:>5}  {format_seconds(model_seconds):>8}  {tokens:>7}  {format_token_speed(speed):>9}  {format_seconds(tools_seconds):>8}")

# Print elapsed seconds for a timed step
def log_timing(label, start_time):
    print(f"{label}: {format_seconds(time.perf_counter() - start_time)}")

# Print elapsed seconds for a stored duration
def log_elapsed(label, seconds):
    print(f"{label}: {format_seconds(seconds)}")

# Format seconds for timing output
def format_seconds(seconds):
    return f"{seconds:.1f}s"

# Format generated tokens per second
def format_token_speed(speed):
    return f"{speed:.1f}/s"

# Let talk.py register itself when running as __main__
def set_talk_module(module):
    voice.set_talk_module(module)

# Main
if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        print(f"LLM unavailable at {API_URL}: {error.reason}")
        print(f"Start the model server first, run {SERVER_SCRIPT}")
        sys.exit(1)
