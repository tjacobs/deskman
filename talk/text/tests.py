#!/usr/bin/env python3

# Tool tests for ask.py, one ask per tool

# Imports
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import accounts.google
import accounts.sonos
import ask
import client
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
NESTED_TEST = os.environ.get("SPEAK_NESTED_TEST") == "1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(SCRIPT_DIR, "server.sh")
SERVER_START_SECONDS = 180
SERVER_POLL_SECONDS = 0.5

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
    # {"name": "look", "prompt": "Look center.", "tools": ["look"], "rejects": ["unavailable", "failed"]},
]
if accounts.google.account_is_configured():
    TOOL_TESTS.append({"name": "get_next_calendar_event", "prompt": "What is my next calendar event?", "tools": ["get_next_calendar_event"]})
if accounts.sonos.sonos_is_configured():
    TOOL_TESTS.append({"name": "list_sonos_speakers", "prompt": "List the Sonos speakers.", "tools": ["list_sonos_speakers"]})

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
    server_process = None

    # Print banner when run standalone
    if not NESTED_TEST:
        print("Testing...")
    print(f"Model: {ask.resolve_model_name()}", flush=True)

    # Start the local text server when it is not already up
    server_ready, server_process = ensure_text_server()
    if not server_ready:
        return False

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
        stop_text_server(server_process)

    # Exit with pass or fail, parent ./test.py prints the suite result when nested
    if failed:
        if not NESTED_TEST:
            print_fail("One or more tests")
        return False
    if not NESTED_TEST:
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
    if ask_error and error is None:
        error = f"ask error: {ask_error}"
    if error:
        print(f"{RED}FAIL{RESET}", flush=True)
        print(error, flush=True)
        if ask_error and not str(error).startswith("ask error:"):
            print(f"ask error: {ask_error}", flush=True)
        return False
    print(f"{GREEN}PASS{RESET}", flush=True)
    return True

# Reuse a healthy server, or start server.sh and wait for /health
def ensure_text_server():
    if text_server_healthy():
        return True, None
    if not os.access(SERVER_SCRIPT, os.X_OK):
        print_fail(f"Text server missing at {SERVER_SCRIPT}. Run ./install.sh --talk first")
        return False, None

    # Start server.sh in the background
    print("Starting text server...", flush=True)
    load_start = time.perf_counter()
    process = subprocess.Popen([SERVER_SCRIPT], cwd=SCRIPT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

    # Wait until the health endpoint answers
    deadline = time.time() + SERVER_START_SECONDS
    while time.time() < deadline:
        if process.poll() is not None:
            print_fail("Text server failed to start. Run ./server.sh to see the error")
            return False, None
        if text_server_healthy():
            print(f"Text server ready in {time.perf_counter() - load_start:.1f} sec", flush=True)
            return True, process
        time.sleep(SERVER_POLL_SECONDS)

    # Timed out waiting for the model to load
    stop_text_server(process)
    print_fail(f"Text server did not become ready within {SERVER_START_SECONDS} sec")
    return False, None

# Return true when the local text model health endpoint answers
def text_server_healthy():
    try:
        request = urllib.request.Request(client.HEALTH_URL, headers={"Authorization": f"Bearer {client.API_KEY}"})
        urllib.request.urlopen(request, timeout=2)
        return True
    except urllib.error.URLError:
        return False

# Stop a text server that these tests started
def stop_text_server(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

# Run one case through the model
def run_one_tool_test(case):
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
    haystack = f"{reply}\n" + "\n".join(ask.last_tool_log)
    for needle in case.get("contains") or []:
        if needle.lower() not in haystack.lower():
            return f"missing {needle!r} in reply or tool results"

    # Fail when the tool reported unavailable or similar
    for needle in case.get("rejects") or []:
        if needle.lower() in haystack.lower():
            return f"got rejected text {needle!r} in reply or tool results"
    return None

# Tool names recorded for the last ask
def tools_used_from_log():
    used = []
    for line in ask.last_tool_log:
        match = re.match(r"\[Tool\] (\S+)", line)
        if match:
            used.append(match.group(1))
    return used

# Record tools during tests, keep long tool bodies out of the console
def record_tool_for_tests(name, arguments, result):
    ask.last_tool_log.append(f"[Tool] {name} {arguments} -> {result}")
    display = result
    if name in ("load_talk_log", "get_system_info"):
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
