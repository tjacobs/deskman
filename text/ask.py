#!/usr/bin/env python3

# Imports
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Import ask modules
import accounts.google
import accounts.sonos
import client
import dates
import maths
import memory
import move
import reminders
import system
import talks
import voice
import volume

# Config paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
PROMPT_PATH = os.path.join(SPEAK_DIR, "prompt.json")
SERVER_SCRIPT = os.path.join(SCRIPT_DIR, "server.sh")

# Config model
DEFAULT_MODEL = client.DEFAULT_MODEL
DEFAULT_CONTEXT_SIZE = client.DEFAULT_CONTEXT_SIZE
MAX_TOKENS = client.MAX_TOKENS

# Config values
TEMPERATURE = 0.7
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 40
INFERENCE_INPUT_CHARS = 200

# Tools the local model can call
TOOLS = move.TOOLS + dates.TOOLS + maths.TOOLS + memory.TOOLS + reminders.TOOLS + talks.TOOLS + system.TOOLS + voice.TOOLS + volume.TOOLS + accounts.sonos.TOOLS + accounts.google.TOOLS
TOOL_NAMES = [tool["function"]["name"] for tool in TOOLS]
TOOL_NAME_SET = set(TOOL_NAMES)

# Conversation history kept across asks in this process
conversation_history = []
last_tool_log = []

# Timing for the last ask, only printed when run standalone
show_timing = False
last_completion_tokens = 0
ask_start = None
resolved_model = None
resolved_context_size = None

# Main
def main():
    # Parse prompt
    prompt, print_prompt, clear_cache, run_tests = parse_args()

    # Run the tool suite and exit with its status
    if run_tests:
        import tests
        sys.exit(0 if tests.run_tool_tests() else 1)

    # Print timing when run standalone, talk.py keeps its own output clean
    global show_timing, ask_start
    show_timing = True
    ask_start = None

    # Drop the server KV cache so this ask pays full prefill cost
    if clear_cache:
        clear_prompt_cache()

    # Show which model the server is running
    print(f"Model: {resolve_model_name()}", flush=True)

    # Show the full request context when asked, messages, tools, and rendered prompt
    if print_prompt:
        print_context(prompt)

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
    run_tests = False
    words = []
    for argument in sys.argv[1:]:
        if argument == "--prompt":
            print_prompt = True
            continue
        if argument == "--clear":
            clear_cache = True
            continue
        if argument == "--test":
            run_tests = True
            continue
        if argument in ("-h", "--help"):
            print_usage()
            sys.exit(0)
        words.append(argument)
    return " ".join(words) if words else "Say hello.", print_prompt, clear_cache, run_tests

# Print usage help
def print_usage():
    print("Usage: ./ask.py [--prompt] [--clear] [--test] [question...]")
    print("  --prompt  print the full model context, messages, tools, and rendered prompt")
    print("  --clear   clear the server cache before asking")
    print("  --test    run tests.py, one ask per tool")
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
    last_talk_log_result = None
    last_system_info_result = None

    # Loop until the model replies with spoken text
    for _ in range(MAX_TOOL_ROUNDS):
        # Call model inference
        message = chat_completion(messages)

        # Get native tool calls, or JSON tool calls from content on 1b
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            tool_calls = parse_tool_calls_from_content(message.get("content") or "")
            if tool_calls:
                message = dict(message)
                message["tool_calls"] = tool_calls
                message["content"] = ""

        # No tool call
        if not tool_calls:
            # Get the spoken text the model returned
            reply = (message.get("content") or "").strip()

            # Force or apply set_reminder when the model skipped or set the wrong one
            if reminders.needs_set_reminder(prompt) and not reminders.wanted_reminder_is_set(prompt):
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

            # Force get_time when the model skipped the clock
            if dates.needs_get_time(prompt) and "get_time" not in used:
                forced = dates.force_get_time(prompt, messages, message, "time" in retried, record_tool)
                if forced is True:
                    retried.add("time")
                    continue
                if forced:
                    used.add("get_time")
                    messages.append({"role": "user", "content": f"Tool results:\nget_time: {forced}\nAnswer using only these results in one or two short sentences."})
                    continue

            # Force get_date when the model skipped today's date
            if dates.needs_get_date(prompt) and "get_date" not in used:
                forced = dates.force_get_date(prompt, messages, message, "date_tool" in retried, record_tool)
                if forced is True:
                    retried.add("date_tool")
                    continue
                if forced:
                    used.add("get_date")
                    messages.append({"role": "user", "content": f"Tool results:\nget_date: {forced}\nAnswer using only these results in one or two short sentences."})
                    continue

            # Force get_day when the model skipped the weekday
            if dates.needs_get_day(prompt) and "get_day" not in used:
                forced = dates.force_get_day(prompt, messages, message, "day" in retried, record_tool)
                if forced is True:
                    retried.add("day")
                    continue
                if forced:
                    used.add("get_day")
                    messages.append({"role": "user", "content": f"Tool results:\nget_day: {forced}\nAnswer using only these results in one or two short sentences."})
                    continue

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

            # Force or apply set_sonos_volume when the model skipped a Sonos volume change
            if accounts.sonos.needs_set_sonos_volume(prompt) and "set_sonos_volume" not in used:
                forced = accounts.sonos.force_set_sonos_volume(prompt, messages, message, "sonos" in retried, record_tool)
                action = apply_forced(forced, messages, "sonos", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Force or apply pause_sonos when the model skipped a Sonos pause
            if accounts.sonos.needs_pause_sonos(prompt) and "pause_sonos" not in used:
                forced = accounts.sonos.force_pause_sonos(prompt, messages, message, "sonos" in retried, record_tool)
                action = apply_forced(forced, messages, "sonos", retried)
                if action == "retry":
                    continue
                if action:
                    return action

            # Force or apply play_sonos when the model skipped a Sonos play
            if accounts.sonos.needs_play_sonos(prompt) and "play_sonos" not in used:
                forced = accounts.sonos.force_play_sonos(prompt, messages, message, "sonos" in retried, record_tool)
                action = apply_forced(forced, messages, "sonos", retried)
                if action == "retry":
                    continue
                if action:
                    return action

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

            # Force load_talk_log when the model skipped loading a day log
            if talks.needs_load_talk_log(prompt) and "load_talk_log" not in used:
                forced = talks.force_load_talk_log(prompt, messages, message, "talks" in retried, record_tool)
                if forced is True:
                    retried.add("talks")
                    continue
                if forced:
                    last_talk_log_result = forced
                    used.add("load_talk_log")
                    messages.append({"role": "user", "content": f"Tool results:\nload_talk_log: {forced}\nAnswer using only these results in one or two short sentences."})
                    continue

            # Force get_system_info when the model skipped hardware or model info
            if system.needs_system_info(prompt) and "get_system_info" not in used:
                forced = system.force_get_system_info(prompt, messages, message, "system" in retried, record_tool)
                if forced is True:
                    retried.add("system")
                    continue
                if forced:
                    last_system_info_result = forced
                    used.add("get_system_info")
                    messages.append({"role": "user", "content": f"Tool results:\nget_system_info: {forced}\nAnswer using only these results in one or two short sentences."})
                    continue

            # Prefer a clear spoken confirmation after a successful volume set
            if not reply and "set_volume" in used:
                reply = volume.confirm_volume_set() or reply

            # Fall back when the model returned no spoken text
            if not reply:
                reply = "Okay."

            # Remember the turn
            remember_turn(messages, reply)

            # Return the spoken text
            return reply

        # Show the tool call JSON the model returned
        if show_timing:
            print(f"Result: {format_tool_calls_json(tool_calls)}", flush=True)

        # Run each tool, then feed results back in a form this model accepts
        content_tools = uses_content_tools()
        if content_tools:
            messages.append({"role": "assistant", "content": format_tool_calls_json(tool_calls)})
        else:
            messages.append(message)
        tool_result_lines = []
        for tool_call in tool_calls:
            voice.inject_list_voices_count(tool_call, prompt)

            # 1b often omits calculate.expression, fill a simple one from the user prompt
            if content_tools:
                fill_content_calculate_expression(tool_call, prompt)

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
            if tool_name == "load_talk_log":
                last_talk_log_result = result
            if tool_name == "get_system_info":
                last_system_info_result = result
            if content_tools:
                tool_result_lines.append(f"{tool_name}: {result}")
            else:
                messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result})

        # Gemma 3 has no tool role, so return results as the next user turn
        if content_tools:
            messages.append({"role": "user", "content": "Tool results:\n" + "\n".join(tool_result_lines) + "\nAnswer using only these results in one or two short sentences."})

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

        # If it skipped or set the wrong reminder, apply the wanted one in Python now
        if reminders.needs_set_reminder(prompt) and not reminders.wanted_reminder_is_set(prompt):
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

        # If it asked for a talk log but skipped the tool, load it in Python now
        if talks.needs_load_talk_log(prompt) and "load_talk_log" not in used:
            forced = talks.force_load_talk_log(prompt, messages, None, True, record_tool)
            if forced:
                last_talk_log_result = forced
                used.add("load_talk_log")
                messages.append({"role": "user", "content": f"Tool results:\nload_talk_log: {forced}\nAnswer using only these results in one or two short sentences."})
                continue

        # If it asked for system info but skipped the tool, read it in Python now
        if system.needs_system_info(prompt) and "get_system_info" not in used:
            forced = system.force_get_system_info(prompt, messages, None, True, record_tool)
            if forced:
                last_system_info_result = forced
                used.add("get_system_info")
                messages.append({"role": "user", "content": f"Tool results:\nget_system_info: {forced}\nAnswer using only these results in one or two short sentences."})
                continue

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

        # If it skipped Sonos volume, set it in Python now
        if accounts.sonos.needs_set_sonos_volume(prompt) and "set_sonos_volume" not in used:
            forced = accounts.sonos.force_set_sonos_volume(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "sonos", retried)
            if action and action != "retry":
                return action

        # If it skipped Sonos pause, pause it in Python now
        if accounts.sonos.needs_pause_sonos(prompt) and "pause_sonos" not in used:
            forced = accounts.sonos.force_pause_sonos(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "sonos", retried)
            if action and action != "retry":
                return action

        # If it skipped Sonos play, play it in Python now
        if accounts.sonos.needs_play_sonos(prompt) and "play_sonos" not in used:
            forced = accounts.sonos.force_play_sonos(prompt, messages, None, True, record_tool)
            action = apply_forced(forced, messages, "sonos", retried)
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
    system_prompt = load_system_prompt()

    # Teach 1b to emit tool JSON in content, its chat template has no tool path
    if uses_content_tools():
        system_prompt = system_prompt + "\n\n" + client.content_tools_instruction(TOOL_NAMES)

    messages = [{"role": "system", "content": system_prompt}]

    # Load previous turns in conversation history
    messages.extend(conversation_history)

    # Put memories and reminders on the user turn, Gemma 3 only allows one system message
    user_content = prompt
    extras = prompt_extras()
    if extras:
        user_content = extras + "\n\n" + prompt

    # Add this question last
    messages.append({"role": "user", "content": user_content})
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

# Long-term memories and reminders for the user turn, empty when none are saved
def prompt_extras():
    extras = []
    memory_text = memory.format_memories_for_prompt()
    if memory_text:
        extras.append(memory_text)
    reminder_text = reminders.format_reminders_for_prompt()
    if reminder_text:
        extras.append(reminder_text)
    return "\n\n".join(extras)

# Print everything that will go into the model for this ask
def print_context(prompt):
    # Build the same messages and request body chat_completion will send
    messages = build_messages(prompt)
    body = build_request_body(messages)

    # Show the OpenAI-style request payload, including tools JSON
    print("Request:")
    print(json.dumps(body, indent=2))
    print()

    # Show the chat-template rendered prompt when the server is up
    rendered = apply_chat_template(messages, body.get("tools") or [])
    if rendered is None:
        print("Rendered prompt: unavailable, start the model server to see the final context text.")
        print()
        return

    # Count tokens in the rendered prompt when tokenize works
    token_count = count_tokens(rendered)
    if token_count is not None:
        print(f"Rendered prompt ({token_count} tokens):")
    else:
        print("Rendered prompt:")
    print(rendered)
    print()

# Build the chat-completions JSON body for one model call
def build_request_body(messages):
    body = {
        "model": resolve_model_name(),
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    # Native tools only for models whose chat template supports them
    if not uses_content_tools():
        body["tools"] = TOOLS
        body["tool_choice"] = "auto"
    return body

# True when the running model has no native tool_calls channel
def uses_content_tools():
    return resolve_model_name() == client.CONTENT_TOOLS_MODEL

# Ask the server to render messages and tools through the model chat template
def apply_chat_template(messages, tools):
    body = {"messages": messages, "tools": tools}
    request = urllib.request.Request(client.APPLY_TEMPLATE_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {client.API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=client.REQUEST_TIMEOUT_SECONDS) as result:
            payload = json.load(result)
    except urllib.error.URLError:
        return None
    return payload.get("prompt")

# Count tokens in a rendered prompt string
def count_tokens(text):
    body = {"content": text}
    request = urllib.request.Request(client.TOKENIZE_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {client.API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=client.REQUEST_TIMEOUT_SECONDS) as result:
            payload = json.load(result)
    except urllib.error.URLError:
        return None
    tokens = payload.get("tokens")
    if tokens is None:
        return None
    return len(tokens)

# Read the model alias from the running server, fall back to the default
def resolve_model_name():
    global resolved_model
    if resolved_model:
        return resolved_model
    request = urllib.request.Request(client.MODELS_URL, headers={"Authorization": f"Bearer {client.API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=client.REQUEST_TIMEOUT_SECONDS) as result:
            payload = json.load(result)
        models = payload.get("data") or []
        if models:
            resolved_model = models[0].get("id") or DEFAULT_MODEL
            return resolved_model
    except urllib.error.URLError:
        pass
    resolved_model = DEFAULT_MODEL
    return resolved_model

# Read the context window size from the running server
def resolve_context_size():
    global resolved_context_size
    if resolved_context_size:
        return resolved_context_size
    request = urllib.request.Request(client.PROPS_URL, headers={"Authorization": f"Bearer {client.API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=client.REQUEST_TIMEOUT_SECONDS) as result:
            payload = json.load(result)
        settings = payload.get("default_generation_settings") or {}
        context_size = settings.get("n_ctx") or (settings.get("params") or {}).get("n_ctx")
        if context_size:
            resolved_context_size = int(context_size)
            return resolved_context_size
    except (urllib.error.URLError, TypeError, ValueError):
        pass
    resolved_context_size = DEFAULT_CONTEXT_SIZE
    return resolved_context_size

# Send one chat request to server
def chat_completion(messages):
    global last_completion_tokens, ask_start, resolved_model, resolved_context_size

    # Build request body
    body = build_request_body(messages)

    # Tell the user inference has started when run standalone
    if show_timing:
        print(f'Inferencing: "{format_inference_input(messages)}"', flush=True)
        if ask_start is None:
            ask_start = time.perf_counter()

    # Send request, client.py asks server.sh to enlarge context when the window is full
    model_start = time.perf_counter()
    response = client.request_chat(body, client.API_KEY, client.REQUEST_TIMEOUT_SECONDS)
    model_seconds = time.perf_counter() - model_start
    resolved_model = None
    resolved_context_size = None

    # Keep the generated token count and print this round's timing now
    usage = response.get("usage") or {}
    last_completion_tokens = int(usage.get("completion_tokens") or 0)
    log_model_timing(model_seconds, last_completion_tokens, response.get("timings") or {}, usage)

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

    # Load a day's talk conversation log
    if name == "load_talk_log":
        result = talks.run_load_talk_log(arguments)
        record_tool(name, arguments, result)
        return result

    # Read computer and model info
    if name == "get_system_info":
        result = system.run_get_system_info(arguments)
        record_tool(name, arguments, result)
        return result

    # Control Sonos speakers or groups
    if name == "list_sonos_speakers":
        result = accounts.sonos.run_list_sonos_speakers(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "play_sonos":
        result = accounts.sonos.run_play_sonos(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "pause_sonos":
        result = accounts.sonos.run_pause_sonos(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "set_sonos_volume":
        result = accounts.sonos.run_set_sonos_volume(arguments)
        record_tool(name, arguments, result)
        return result

    # Read Google Calendar events
    if name == "list_calendar_events":
        result = accounts.google.run_list_calendar_events(arguments)
        record_tool(name, arguments, result)
        return result
    if name == "get_next_calendar_event":
        result = accounts.google.run_get_next_calendar_event(arguments)
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

# Turn JSON in assistant content into OpenAI-style tool_calls, or empty
def parse_tool_calls_from_content(content):
    data = extract_json_object(content)
    if data is None:
        return []

    # Shape B, {"name":"get_time","arguments":{}}
    if isinstance(data.get("name"), str):
        tool_call = make_content_tool_call(data.get("name"), data.get("arguments"), 0)
        return [tool_call] if tool_call else []

    # Shape C, {"tool_calls":[{"function":{"name":"...","arguments":"{}"}}]}
    raw_calls = data.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    tool_calls = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function") or raw_call
        if not isinstance(function, dict):
            continue
        tool_call = make_content_tool_call(function.get("name"), function.get("arguments"), index)
        if tool_call:
            tool_calls.append(tool_call)
    return tool_calls

# Build one synthetic tool_call dict when the name is a known tool
def make_content_tool_call(name, arguments, index):
    if name not in TOOL_NAME_SET:
        return None
    parsed_arguments = parse_tool_arguments(arguments)
    return {
        "id": f"content_call_{index}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(parsed_arguments),
        },
    }

# Fill calculate.expression when 1b returned the tool with empty arguments
def fill_content_calculate_expression(tool_call, prompt):
    function = tool_call.get("function") or {}
    if function.get("name") != "calculate":
        return
    arguments = parse_tool_arguments(function.get("arguments"))
    if str(arguments.get("expression") or "").strip():
        return
    expression = maths.expression_from_prompt(prompt)
    if not expression:
        return
    arguments["expression"] = expression
    function["arguments"] = json.dumps(arguments)
    tool_call["function"] = function

# Pull the first JSON object from model text, stripping markdown fences
def extract_json_object(content):
    text = strip_content_fences(content)
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return recover_tool_json(text)
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return recover_tool_json(text)
    if not isinstance(data, dict):
        return None
    return data

# Strip markdown code fences around JSON
def strip_content_fences(content):
    text = (content or "").strip()
    if not text:
        return ""
    if "```" not in text:
        return text
    fenced = text.split("```")[1]
    if fenced.startswith("json"):
        fenced = fenced[4:]
    return fenced.strip()

# Recover {"name","arguments"} when the model emits broken JSON
def recover_tool_json(text):
    name_match = re.search(r'"name"\s*:\s*"([a-z_]+)"', text)
    if not name_match:
        return None
    name = name_match.group(1)
    if name not in TOOL_NAME_SET:
        return None
    arguments = {}
    arguments_match = re.search(r'"arguments"\s*:\s*(\{(?:[^{}]|\{[^{}]*\})*\})', text)
    if arguments_match:
        try:
            arguments = json.loads(arguments_match.group(1))
        except json.JSONDecodeError:
            arguments = {}
    return {"name": name, "arguments": arguments}

# Print timing for one finished model call
def log_model_timing(model_seconds, tokens, timings, usage):
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
            f"Speed: {format_token_speed(speed)}  "
            f"Context: {format_context_usage(usage, timings)}",
            flush=True,
        )
        return

    # Fall back when the server omits timings
    speed = tokens / model_seconds if model_seconds > 0 else 0
    print(f"Model: {format_seconds(model_seconds)}  Tokens: {tokens}  Speed: {format_token_speed(speed)}  Context: {format_context_usage(usage, timings)}", flush=True)

# Format how full the context window is after this round
def format_context_usage(usage, timings):
    context_size = resolve_context_size()
    used_tokens = int(usage.get("total_tokens") or 0)
    if used_tokens <= 0:
        used_tokens = int(timings.get("cache_n") or 0) + int(timings.get("prompt_n") or 0) + int(timings.get("predicted_n") or 0)
    percent = (100.0 * used_tokens / context_size) if context_size > 0 else 0
    return f"{used_tokens}/{context_size} ({percent:.0f}%)"

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

        # Drop the memories prefix so timing lines show just the question
        extras = prompt_extras()
        if role == "user" and extras and content.startswith(extras):
            content = content[len(extras):].lstrip()

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
    list_request = urllib.request.Request(client.CACHE_URL, headers={"Authorization": f"Bearer {client.API_KEY}"})
    try:
        with urllib.request.urlopen(list_request, timeout=client.REQUEST_TIMEOUT_SECONDS) as result:
            caches = json.load(result)
    except urllib.error.URLError as error:
        print(f"Could not list cache at {client.CACHE_URL}: {error.reason}")
        print(f"Start the model server first, run {SERVER_SCRIPT}")
        sys.exit(1)

    # Erase each cache, empty body avoids a hang on some llama-server builds
    for cache in caches:
        cache_id = cache.get("id", 0)
        erase_url = f"{client.CACHE_URL}/{cache_id}?action=erase"
        erase_request = urllib.request.Request(erase_url, data=b"", method="POST", headers={"Authorization": f"Bearer {client.API_KEY}", "Content-Length": "0"})
        try:
            with urllib.request.urlopen(erase_request, timeout=client.REQUEST_TIMEOUT_SECONDS) as result:
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
        print(f"LLM unavailable at {client.API_URL}: {error.reason}")
        print(f"Start the model server first, run {SERVER_SCRIPT}")
        sys.exit(1)
