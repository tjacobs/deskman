#!/usr/bin/env python3

# Imports
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Import ask modules
import dates
import maths
import memory
import move
import reminders
import voice
import volume

# Config
API_BASE = "http://127.0.0.1:8080"
API_URL = f"{API_BASE}/v1/chat/completions"
CACHE_URL = f"{API_BASE}/slots"
API_KEY = "local"
MODEL = "gemma-4-e2b"

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
PROMPT_PATH = os.path.join(SPEAK_DIR, "prompt.json")
SERVER_SCRIPT = os.path.join(SCRIPT_DIR, "server.sh")

# Config values
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 100
TEMPERATURE = 0.7
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 40
INFERENCE_INPUT_CHARS = 200

# Tools the local model can call
TOOLS = move.TOOLS + dates.TOOLS + maths.TOOLS + memory.TOOLS + reminders.TOOLS + voice.TOOLS + volume.TOOLS

# Conversation history kept across asks in this process
conversation_history = []
last_tool_log = []

# Timing for the last ask, only printed when run standalone
show_timing = False
last_completion_tokens = 0
ask_start = None

# Main
def main():
    # Parse prompt
    prompt, print_prompt, clear_cache = parse_args()

    # Print timing when run standalone, talk.py keeps its own output clean
    global show_timing, ask_start
    show_timing = True
    ask_start = None

    # Drop the server KV cache so this ask pays full prefill cost
    if clear_cache:
        clear_prompt_cache()

    # Show the system prompt sent to the model when asked
    if print_prompt:
        print("System prompt:")
        print(system_prompt_with_memories())
        print()

    # Ask local model
    response = ask_model(prompt)
    print(response)

    # Print total time when the model was called
    if ask_start is not None:
        log_timing("Total", ask_start)

# Parse prompt from command line
def parse_args():
    # Pull out flags, then join the remaining words into the user prompt
    print_prompt = False
    clear_cache = False
    words = []
    for argument in sys.argv[1:]:
        if argument == "--prompt":
            print_prompt = True
            continue
        if argument == "--clear":
            clear_cache = True
            continue
        if argument in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        words.append(argument)
    return " ".join(words) if words else "Say hello.", print_prompt, clear_cache

# Print usage help
def print_usage():
    print("Usage: ./ask.py [--prompt] [--clear] [question...]")
    print("  --prompt  print the system prompt at start")
    print("  --clear   clear the server cache before asking")
    print("  (no arg)  say hello.")

# Ask the model, running any tool calls it requests
def ask_model(prompt):
    # Start a fresh tool log for this ask
    last_tool_log.clear()

    # Answer some questions in Python without calling the model
    reply = skip_inference(prompt)
    if reply:
        return reply

    # Build the chat messages for this ask
    messages = build_messages(prompt)

    # Track which tools ran and which domains already got one model retry
    used = set()
    retried = set()
    last_calculate_expression = None
    last_calculate_result = None
    last_list_voices_result = None
    last_list_reminders_result = None

    # Loop until the model replies with spoken text
    for _ in range(MAX_TOOL_ROUNDS):
        # Time one model call and print its timing as soon as it returns
        message = chat_completion(messages)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            reply = (message.get("content") or "").strip()

            # Force or apply set_reminder when the model skipped scheduling
            if reminders.needs_set_reminder(prompt) and "set_reminder" not in used:
                forced = reminders.force_set_reminder(prompt, messages, message, "reminder" in retried, record_tool)
                action = apply_forced(forced, messages, "reminder", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Force or apply cancel_reminder when the model skipped canceling
            if reminders.needs_cancel_reminder(prompt) and "cancel_reminder" not in used:
                forced = reminders.force_cancel_reminder(prompt, messages, message, "reminder" in retried, record_tool)
                action = apply_forced(forced, messages, "reminder", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Prefer the exact list_reminders tool text when listing
            if reminders.needs_list_reminders(prompt) and not reminders.needs_set_reminder(prompt) and not reminders.needs_cancel_reminder(prompt):
                if last_list_reminders_result:
                    remember_turn(messages, last_list_reminders_result)
                    return last_list_reminders_result
                if "list_reminders" not in used:
                    forced = reminders.force_list_reminders(prompt, messages, message, "reminder" in retried, record_tool)
                    action = apply_forced(forced, messages, "reminder", retried)
                    if action == "retry":
                        continue
                    if action:
                        return action

            # Force or apply remember when the model skipped saving a fact
            if memory.needs_remember(prompt) and not reminders.needs_set_reminder(prompt) and "remember" not in used:
                forced = memory.force_remember(prompt, messages, message, "memory" in retried, record_tool)
                action = apply_forced(forced, messages, "memory", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Force or apply forget when the model skipped dropping a fact
            if memory.needs_forget(prompt) and not reminders.needs_cancel_reminder(prompt) and "forget" not in used:
                forced = memory.force_forget(prompt, messages, message, "memory" in retried, record_tool)
                action = apply_forced(forced, messages, "memory", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Prefer a date expression already calculated, else force Python date math
            if dates.needs_date_math(prompt):
                spoken = dates.speak_model_date_calculate(last_calculate_expression, last_calculate_result)
                if spoken:
                    remember_turn(messages, spoken)
                    return spoken
                forced = dates.force_date_calculate(prompt, messages, message, "date" in retried, record_tool)
                action = apply_forced(forced, messages, "date", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Force calculate when the model guessed arithmetic
            if maths.needs_math_tool(prompt) and not dates.needs_date_math(prompt) and "calculate" not in used and "math" not in retried:
                retried.add("math")
                print(f'Result: "{reply or ""}"', flush=True)
                print(f'Missing math tool, retrying, why: {maths.math_tool_reason(prompt)}, but it answered without calling calculate.', flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": maths.MATH_RETRY_PROMPT})
                continue

            # Force or apply set_volume when the model skipped a volume change
            if volume.needs_set_volume(prompt) and "set_volume" not in used:
                forced = volume.force_set_volume(prompt, messages, message, "volume" in retried, record_tool)
                action = apply_forced(forced, messages, "volume", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Force get_volume when the model guessed the current volume
            if volume.needs_get_volume(prompt) and "get_volume" not in used and "volume" not in retried:
                retried.add("volume")
                print("[ask] missing get_volume, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": volume.GET_VOLUME_RETRY_PROMPT})
                continue

            # Force or apply set_voice when the model skipped a voice change
            if voice.needs_set_voice(prompt) and "set_voice" not in used:
                forced = voice.force_set_voice(prompt, messages, message, "voice" in retried, record_tool)
                action = apply_forced(forced, messages, "voice", retried)
                if action == "retry":
                    continue
                if action:
                    return action

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
            if not reply and "set_volume" in used:
                reply = volume.confirm_volume_set() or reply
            remember_turn(messages, reply or "Okay.")
            return reply or "Okay."

        # Show the tool call JSON the model returned
        if show_timing:
            print(f"Result: {format_tool_calls_json(tool_calls)}", flush=True)

        # Keep the assistant tool call turn, then return each tool result
        messages.append(message)
        for tool_call in tool_calls:
            voice.inject_list_voices_count(tool_call, prompt)
            result = run_tool(tool_call)
            tool_name = (tool_call.get("function") or {}).get("name")
            if tool_name:
                used.add(tool_name)
            if tool_name == "calculate":
                arguments = parse_tool_arguments((tool_call.get("function") or {}).get("arguments"))
                last_calculate_expression = arguments.get("expression", "")
                last_calculate_result = result
            if tool_name == "list_voices":
                last_list_voices_result = result
            if tool_name == "list_reminders":
                last_list_reminders_result = result
            messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result})

        # Resolve day counts in Python, or keep a valid date expression the model already ran
        if dates.needs_date_math(prompt):
            forced = dates.force_date_calculate(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "date", retried)
            if action and action != "retry":
                return action
            spoken = dates.speak_model_date_calculate(last_calculate_expression, last_calculate_result)
            if spoken:
                remember_turn(messages, spoken)
                return spoken

        # If it talked about reminders but skipped the tool, apply it in Python now
        if reminders.needs_set_reminder(prompt) and "set_reminder" not in used:
            forced = reminders.force_set_reminder(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "reminder", retried)
            if action and action != "retry":
                return action
        if reminders.needs_cancel_reminder(prompt) and "cancel_reminder" not in used:
            forced = reminders.force_cancel_reminder(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "reminder", retried)
            if action and action != "retry":
                return action
        if reminders.needs_list_reminders(prompt) and last_list_reminders_result and not reminders.needs_set_reminder(prompt) and not reminders.needs_cancel_reminder(prompt):
            remember_turn(messages, last_list_reminders_result)
            return last_list_reminders_result

        # If it talked about remembering but did not call remember, save it in Python now
        if memory.needs_remember(prompt) and not reminders.needs_set_reminder(prompt) and "remember" not in used:
            forced = memory.force_remember(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "memory", retried)
            if action and action != "retry":
                return action

        # If it talked about forgetting but did not call forget, drop it in Python now
        if memory.needs_forget(prompt) and not reminders.needs_cancel_reminder(prompt) and "forget" not in used:
            forced = memory.force_forget(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "memory", retried)
            if action and action != "retry":
                return action

        # If it checked volume instead of setting it, set it in Python now
        if volume.needs_set_volume(prompt) and "set_volume" not in used:
            forced = volume.force_set_volume(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "volume", retried)
            if action == "retry":
                used.add("set_volume")
                continue
            if action:
                return action

        # If it listed voices instead of setting one, set it in Python now
        if voice.needs_set_voice(prompt) and "set_voice" not in used:
            forced = voice.force_set_voice(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "voice", retried)
            if action == "retry":
                used.add("set_voice")
                continue
            if action:
                return action

        # Speak the exact voice list from the tool, do not let the model shorten it
        if voice.needs_list_voices(prompt) and last_list_voices_result and not voice.needs_set_voice(prompt):
            remember_turn(messages, last_list_voices_result)
            return last_list_voices_result

    # Give up after too many tool rounds
    reply = "I could not finish that request."
    remember_turn(messages, reply)
    return reply

# Build the chat messages for one ask
def build_messages(prompt):
    # Start with prompt.json alone so that prefix stays identical across asks for KV cache reuse
    messages = [{"role": "system", "content": load_system_prompt()}]

    # Load memories in a second system message so new tokens append after the cached system prompt
    extras = prompt_extras()
    if extras:
        messages.append({"role": "system", "content": extras})

    # Load previous turns in conversation history
    messages.extend(conversation_history)

    # Add this question last
    messages.append({"role": "user", "content": prompt})
    return messages

# Handle a forced tool result, True means retry the model, text means return that reply
def apply_forced(forced, messages, domain, retried):
    if forced is True:
        retried.add(domain)
        return "retry"
    if forced:
        remember_turn(messages, forced)
        return forced
    return None

# Answer date questions in Python when the model would only guess
def skip_inference(prompt):
    # Answer day counts with the next matching future date
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

    # Inference needed
    return None

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

# Load the system prompt from ../prompt.json
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

# Long-term memories and reminders for a second system message, empty when none are saved
def prompt_extras():
    extras = []
    memory_text = memory.format_memories_for_prompt()
    if memory_text:
        extras.append(memory_text)
    reminder_text = reminders.format_reminders_for_prompt()
    if reminder_text:
        extras.append(reminder_text)
    return "\n\n".join(extras)

# Full prompt text for --prompt, system plus extras
def system_prompt_with_memories():
    prompt = load_system_prompt()
    extras = prompt_extras()
    if not extras:
        return prompt
    return prompt + "\n\n" + extras

# Send one chat request to server
def chat_completion(messages):
    global last_completion_tokens, ask_start

    # Build request body
    body = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    # Tell the user inference has started when run standalone
    if show_timing:
        print(f'Inferencing: "{format_inference_input(messages)}"', flush=True)
        if ask_start is None:
            ask_start = time.perf_counter()

    # Send request
    model_start = time.perf_counter()
    request = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as result:
        response = json.load(result)
    model_seconds = time.perf_counter() - model_start

    # Keep the generated token count and print this round's timing now
    usage = response.get("usage") or {}
    last_completion_tokens = int(usage.get("completion_tokens") or 0)
    log_model_timing(model_seconds, last_completion_tokens, response.get("timings") or {})

    # Return the assistant message
    return response["choices"][0]["message"]

# Run one tool call and return a short result string
def run_tool(tool_call):
    # Read the function name and arguments
    function = tool_call.get("function") or {}
    name = function.get("name", "")
    arguments = parse_tool_arguments(function.get("arguments"))

    # Turn the head
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
    line = f"[Tool] {name} {arguments} -> {result}"
    last_tool_log.append(line)
    print(line, flush=True)

# One-line JSON for the tool calls the model returned
def format_tool_calls_json(tool_calls):
    calls = []
    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        calls.append({"name": function.get("name", ""), "arguments": parse_tool_arguments(function.get("arguments"))})
    if len(calls) == 1:
        return json.dumps(calls[0], separators=(",", ":"))
    return json.dumps(calls, separators=(",", ":"))

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

# Print timing for one finished model call
def log_model_timing(model_seconds, tokens, timings):
    # Skip when not asked to show timing
    if not show_timing:
        return

    # Prefer server prefill/generate split, a cold system prompt shows up as a long Prefill
    if timings:
        prefill_seconds = float(timings.get("prompt_ms") or 0) / 1000
        generate_seconds = float(timings.get("predicted_ms") or 0) / 1000
        cached_tokens = int(timings.get("cache_n") or 0)
        prefill_tokens = int(timings.get("prompt_n") or 0)
        generate_tokens = int(timings.get("predicted_n") or tokens)
        speed = float(timings.get("predicted_per_second") or 0)
        if speed <= 0 and generate_seconds > 0:
            speed = generate_tokens / generate_seconds
        print(
            f"Model: {format_seconds(model_seconds)}  "
            f"Prefill: {format_seconds(prefill_seconds)} ({cached_tokens} cached, {prefill_tokens} new)  "
            f"Generate: {format_seconds(generate_seconds)}  "
            f"Tokens: {generate_tokens}  "
            f"Speed: {format_token_speed(speed)}",
            flush=True,
        )
        return

    # Fall back when the server omits timings
    speed = tokens / model_seconds if model_seconds > 0 else 0
    print(f"Model: {format_seconds(model_seconds)}  Tokens: {tokens}  Speed: {format_token_speed(speed)}", flush=True)

# Short preview of the newest text the model is reacting to
def format_inference_input(messages):
    # Show the newest text the model is reacting to
    for message in reversed(messages):
        content = (message.get("content") or "").strip()
        if not content:
            continue
        role = message.get("role", "")
        if role not in ("user", "tool"):
            continue
        text = " ".join(content.split())
        if len(text) > INFERENCE_INPUT_CHARS:
            text = text[:INFERENCE_INPUT_CHARS - 3] + "..."
        if role == "tool":
            return f"[tool] {text}"
        return text
    return "(no input)"

# Print elapsed seconds for a timed step
def log_timing(label, start_time):
    print(f"{label}: {format_seconds(time.perf_counter() - start_time)}", flush=True)

# Format seconds for timing output
def format_seconds(seconds):
    return f"{seconds:.1f}s"

# Format generated tokens per second
def format_token_speed(speed):
    return f"{speed:.1f} tokens/sec"

# Erase the server prompt cache so the next ask is a cold prefill
def clear_prompt_cache():
    # List cache contexts, then erase each one
    list_request = urllib.request.Request(CACHE_URL, headers={"Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(list_request, timeout=REQUEST_TIMEOUT_SECONDS) as result:
            caches = json.load(result)
    except urllib.error.URLError as error:
        print(f"Could not list cache at {CACHE_URL}: {error.reason}")
        print(f"Start the model server first, run {SERVER_SCRIPT}")
        sys.exit(1)

    # Erase each cache, empty body avoids a hang on some llama-server builds
    for cache in caches:
        cache_id = cache.get("id", 0)
        erase_url = f"{CACHE_URL}/{cache_id}?action=erase"
        erase_request = urllib.request.Request(erase_url, data=b"", method="POST", headers={"Authorization": f"Bearer {API_KEY}", "Content-Length": "0"})
        try:
            with urllib.request.urlopen(erase_request, timeout=REQUEST_TIMEOUT_SECONDS) as result:
                result.read()
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")
            if error.code == 501:
                print("Server was started without cache support.")
                print(f"Restart it with the updated {SERVER_SCRIPT}, then try --clear again.")
                sys.exit(1)
            print(f"Could not clear cache: {error.code} {body}")
            sys.exit(1)
        except urllib.error.URLError as error:
            print(f"Could not clear cache: {error.reason}")
            sys.exit(1)

    # Print success
    print("Cleared cache.", flush=True)

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
