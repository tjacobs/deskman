#!/usr/bin/env python3

# Imports
import ast
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

# Config
API_URL = "http://127.0.0.1:8080/v1/chat/completions"
API_KEY = "local"
MODEL = "gemma-4-e2b"
DEFAULT_PROMPT = "Introduce yourself in one short sentence."
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH = os.path.join(SCRIPT_DIR, "prompt.json")
DEFAULT_SYSTEM_PROMPT = "I am a robot head on a desk, answering questions. Answer in one or two short sentences suitable for speaking aloud. When asked to look left, right, or center, call the look tool. Never guess the time, date, or day of the week. Always call get_time, get_date, or get_day first, then answer using only that tool result. Never guess arithmetic. Always call calculate with a Python math expression, then answer using only that tool result."
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 100
TEMPERATURE = 0.7
MAX_TOOL_ROUNDS = 4
MAX_HISTORY_MESSAGES = 20
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 60
CLOCK_RETRY_PROMPT = "Do not guess. Call get_time, get_date, or get_day now, then answer using only the tool result."
MATH_RETRY_PROMPT = "Do not guess. Call calculate with a Python math expression now, then answer using only the tool result."
MATH_ENV = {
    "math": math,
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
            "description": "Get the current local time. Required whenever the user asks the time. Never invent the time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": "Get the current local calendar date. Required whenever the user asks the date or today's date. Never invent a date.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_day",
            "description": "Get the current day of the week. Required whenever the user asks what day it is. Never invent the weekday.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a math expression with Python. Required for any arithmetic, multiplication, division, roots, or numeric calculation. Never invent the answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Python math expression, for example 17 * 43 or math.sqrt(144).",
                    },
                },
                "required": ["expression"],
            },
        },
    },
]

# Conversation history kept across asks in this process
conversation_history = []

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
    # Start from the system prompt, prior turns, and this question
    messages = [{"role": "system", "content": load_system_prompt()}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": prompt})
    clock_retry_used = False
    clock_tool_used = False
    math_retry_used = False
    math_tool_used = False

    # Loop until the model replies with spoken text
    for _ in range(MAX_TOOL_ROUNDS):
        message = chat_completion(messages)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            reply = (message.get("content") or "").strip()

            # Force a clock tool when the model guessed time, date, or day
            if needs_clock_tool(prompt) and not clock_tool_used and not clock_retry_used:
                clock_retry_used = True
                print("[ask] missing clock tool, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": CLOCK_RETRY_PROMPT})
                continue

            # Force calculate when the model guessed arithmetic
            if needs_math_tool(prompt) and not math_tool_used and not math_retry_used:
                math_retry_used = True
                print("[ask] missing math tool, retrying", flush=True)
                messages.append(message)
                messages.append({"role": "user", "content": MATH_RETRY_PROMPT})
                continue

            remember_exchange(prompt, reply)
            return reply

        # Keep the assistant tool call turn, then return each tool result
        print(f"[ask] tools: {[call.get('function', {}).get('name') for call in tool_calls]}", flush=True)
        messages.append(message)
        for tool_call in tool_calls:
            result = run_tool(tool_call)
            tool_name = (tool_call.get("function") or {}).get("name")
            if tool_name in ("get_time", "get_date", "get_day"):
                clock_tool_used = True
            if tool_name == "calculate":
                math_tool_used = True
            messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": result})

    # Give up after too many tool rounds
    reply = "I could not finish that request."
    remember_exchange(prompt, reply)
    return reply

# Return true when the question needs a live clock tool
def needs_clock_tool(prompt):
    text = prompt.lower()
    if re.search(r"\bdate\b", text) or re.search(r"\btoday\b", text):
        return True
    if re.search(r"\btime\b", text):
        return True
    return bool(re.search(r"\b(what|which)\s+day\b|\bday\s+(is|of)\b", text))

# Return true when the question needs the Python math tool
def needs_math_tool(prompt):
    text = prompt.lower()
    if any(word in text for word in ("plus", "minus", "times", "divided", "multiply", "square root", "calculate", "percent")):
        return True
    if re.search(r"\d+\s*[\+\-\*\/x×÷]\s*\d+", text):
        return True
    return bool(re.search(r"\b(what is|what's)\s+\d", text))

# Remember one question and spoken reply for later asks
def remember_exchange(prompt, reply):
    conversation_history.append({"role": "user", "content": prompt})
    conversation_history.append({"role": "assistant", "content": reply})

    # Drop the oldest turns when the history grows too long
    while len(conversation_history) > MAX_HISTORY_MESSAGES:
        conversation_history.pop(0)

# Load the system prompt from prompt.json
def load_system_prompt():
    try:
        with open(PROMPT_PATH, encoding="utf-8") as prompt_file:
            data = json.load(prompt_file)
        prompt = str(data.get("system") or "").strip()
        if prompt:
            return prompt
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return DEFAULT_SYSTEM_PROMPT

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
    print(f"[tool] {name} {arguments}", flush=True)

    # Turn the head through robot look.py
    if name == "look":
        result = run_look(arguments)
        print(f"[tool] {name} -> {result}", flush=True)
        return result

    # Return the current clock time
    if name == "get_time":
        result = clock_time()
        print(f"[tool] {name} -> {result}", flush=True)
        return result

    # Return the current calendar date
    if name == "get_date":
        result = calendar_date()
        print(f"[tool] {name} -> {result}", flush=True)
        return result

    # Return the current weekday
    if name == "get_day":
        result = calendar_day()
        print(f"[tool] {name} -> {result}", flush=True)
        return result

    # Evaluate math in Python
    if name == "calculate":
        result = run_calculate(arguments.get("expression", ""))
        print(f"[tool] {name} -> {result}", flush=True)
        return result

    # Unknown tool
    result = f"Unknown tool: {name}"
    print(f"[tool] {name} -> {result}", flush=True)
    return result

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

# Return true when the expression tree is only basic math
def math_expression_safe(tree):
    for node in ast.walk(tree):
        if not isinstance(node, MATH_SAFE_NODES):
            return False
        if isinstance(node, ast.Name) and node.id not in MATH_ENV:
            return False
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name) or node.value.id != "math":
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
    return str(result)
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
