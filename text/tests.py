#!/usr/bin/env python3

# Tool tests for ask.py, one ask per tool

# Imports
import json
import re
import sys

import ask
import memory
import reminders
import system

# Config
TEST_MEMORY_TEXT = "ask test marker is purple elephants"
TEST_REMINDER_NAME = "asktest"
TEST_REMINDER_TIME = "5:30 PM"
STEP_NAME_WIDTH = 16
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# End to end asks that should call these tools
TOOL_TESTS = [
    {"name": "get_time", "prompt": "What time is it?", "tools": ["get_time"]},
    {"name": "get_date", "prompt": "What's the date today?", "tools": ["get_date"]},
    {"name": "get_day", "prompt": "What day of the week is it?", "tools": ["get_day"]},
    {"name": "calculate", "prompt": "What is 17 times 43?", "tools": ["calculate"], "contains": ["731"]},
    {"name": "date_math", "prompt": "How many days until August 15?", "tools": ["calculate"]},
    {"name": "get_system_info", "prompt": "Do you know what computer you're running on?", "tools": ["get_system_info"], "contains": [system.read_hardware_name()]},
    {"name": "get_model_name", "prompt": "What's your model?", "tools": ["get_system_info"], "contains": ["gemma"]},
    {"name": "get_model_size", "prompt": "Are you a 1B or 2B model?", "tools": ["get_system_info"], "contains": ["billion"]},
    {"name": "load_talk_log", "prompt": "Load yesterday's talk log.", "tools": ["load_talk_log"]},
    {"name": "get_volume", "prompt": "What is the volume?", "tools": ["get_volume"]},
    {"name": "list_voices", "prompt": "List the voices.", "tools": ["list_voices"]},
    {"name": "list_reminders", "prompt": "What reminders are set?", "tools": ["list_reminders"]},
    {"name": "remember", "prompt": f"Remember that {TEST_MEMORY_TEXT}.", "tools": ["remember"]},
    {"name": "forget", "prompt": f"Forget that {TEST_MEMORY_TEXT}.", "tools": ["forget"]},
    {"name": "set_reminder", "prompt": f"Remind me about {TEST_REMINDER_NAME} at {TEST_REMINDER_TIME}.", "tools": ["set_reminder"], "contains": [TEST_REMINDER_NAME]},
    {"name": "cancel_reminder", "prompt": f"Cancel the {TEST_REMINDER_NAME} reminder.", "tools": ["cancel_reminder"], "contains": [TEST_REMINDER_NAME]},
    {"name": "look", "prompt": "Look center.", "tools": ["look"], "direct": {"name": "look", "arguments": {"direction": "center"}}},
    {"name": "list_sonos_speakers", "prompt": "List the Sonos speakers.", "tools": ["list_sonos_speakers"], "direct": {"name": "list_sonos_speakers", "arguments": {}}, "contains": ["not configured"]},
    {"name": "get_next_calendar_event", "prompt": "What is my next calendar event?", "tools": ["get_next_calendar_event"], "direct": {"name": "get_next_calendar_event", "arguments": {}}, "contains": ["not configured"]},
]

# Main
def main():
    sys.exit(0 if run_tool_tests() else 1)

# Ask one question per tool and report pass or fail
def run_tool_tests():
    ask.show_timing = False
    ask.ask_start = None
    failed = False
    original_record_tool = ask.record_tool
    ask.record_tool = record_tool_for_tests

    # Print
    print("Testing...")
    print(f"Model: {ask.resolve_model_name()}", flush=True)

    # Clean leftover test memory and reminder before and after the suite
    cleanup_tool_tests()
    try:
        for case in TOOL_TESTS:
            ask.conversation_history = []
            ask.last_tool_log.clear()
            failed |= not run_step(case)
    finally:
        ask.record_tool = original_record_tool
        cleanup_tool_tests()

    # Exit with pass or fail
    if failed:
        print_fail("One or more tests")
        return False
    print_pass("All tests")
    return True

# Run one named test case
def run_step(case):
    name = case["name"]
    print(f"== {name:<{STEP_NAME_WIDTH}} == {case['prompt']}", flush=True)
    reply, ask_error = run_one_tool_test(case)
    error = tool_test_error(case, reply)
    if reply:
        print(reply, flush=True)
    if error is None and ask_error:
        print(f"NOTE: tools ok, later ask error: {ask_error}", flush=True)
    if error:
        print(f"{RED}FAIL{RESET}", flush=True)
        print(error, flush=True)
        if ask_error:
            print(f"ask error: {ask_error}", flush=True)
        return False
    print(f"{GREEN}PASS{RESET}", flush=True)
    return True

# Run one case through the model, or call the tool directly when marked
def run_one_tool_test(case):
    direct = case.get("direct")
    if direct:
        ask.last_tool_log.clear()
        reply = ask.run_tool({"id": "test", "function": {"name": direct["name"], "arguments": json.dumps(direct.get("arguments") or {})}})
        return reply, None
    try:
        return ask.ask_model(case["prompt"]), None
    except Exception as error:
        return "", error

# Return a failure reason when the case did not use the expected tools
def tool_test_error(case, reply):
    used = tools_used_from_log()
    missing = [name for name in case["tools"] if name not in used]
    if missing:
        return f"expected tools {case['tools']}, used {used or ['none']}"

    # Check optional text in the reply or tool log
    for needle in case.get("contains") or []:
        haystack = f"{reply}\n" + "\n".join(ask.last_tool_log)
        if needle.lower() not in haystack.lower():
            return f"missing {needle!r} in reply or tool results"
    return None

# Tool names recorded for the last ask
def tools_used_from_log():
    used = []
    for line in ask.last_tool_log:
        match = re.match(r"\[Tool\] (\S+)", line)
        if match:
            used.append(match.group(1))
    return used

# Record tools during tests, keep talk log bodies out of the console
def record_tool_for_tests(name, arguments, result):
    ask.last_tool_log.append(f"[Tool] {name} {arguments} -> {result}")
    display = result
    if name == "load_talk_log":
        display = result.split("\n", 1)[0]
        if "\n" in result:
            display = display + " ..."
    print(f"[Tool] {name} {arguments} -> {display}", flush=True)

# Drop test memory and reminder side effects
def cleanup_tool_tests():
    memory.run_forget({"text": TEST_MEMORY_TEXT})
    reminders.run_cancel_reminder({"name": TEST_REMINDER_NAME})

# Print pass line
def print_pass(message):
    print(f"{GREEN}PASS{RESET}: {message}")

# Print fail line
def print_fail(message):
    print(f"{RED}FAIL{RESET}: {message}")

# Main
if __name__ == "__main__":
    main()
