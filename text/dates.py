#!/usr/bin/env python3

# Clock, calendar, and day-count date helpers

# Imports
import calendar
import json
import re
import time
from datetime import date

import maths

# Config
DATE_MATH_RETRY_PROMPT = "Do not guess. Call calculate with a date expression using date and today, for example (date(2026, 8, 15) - today()).days, then answer using only the tool result. Never pass a bare number."
TIME_RETRY_PROMPT = "Do not guess. Call get_time now, then answer using only the tool result."
DATE_RETRY_PROMPT = "Do not guess. Call get_date now, then answer using only the tool result."
DAY_RETRY_PROMPT = "Do not guess. Call get_day now, then answer using only the tool result."
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

# Tools the local model can call for the current clock and calendar
TOOLS = [
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
]

# Main
def main():
    # Print the current clock and calendar when run with no args
    print(clock_time())
    print(calendar_date())
    print(calendar_day())

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

# Return true when the user asks for the current clock time
def needs_get_time(prompt):
    text = prompt.lower()
    return bool(re.search(r"\bwhat time\b|\bthe time\b|\bcurrent time\b|\btime is it\b", text))

# Return true when the user asks for today's calendar date
def needs_get_date(prompt):
    text = prompt.lower()
    if needs_get_time(prompt):
        return False
    if needs_date_math(prompt):
        return False
    return bool(re.search(r"\b(what'?s|what is)\s+(the\s+)?date\b|\btoday'?s\s+date\b|\bdate today\b", text))

# Return true when the user asks for the weekday
def needs_get_day(prompt):
    text = prompt.lower()
    if needs_get_time(prompt) or needs_get_date(prompt):
        return False
    return bool(re.search(r"\bday of the week\b|\bwhat day\b|\bwhich day\b|\bweekday\b", text))

# Retry get_time once, then read the clock in Python if still missing
def force_get_time(prompt, messages, message, already_retried, record_tool):
    if not already_retried:
        print("[dates] missing get_time, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": TIME_RETRY_PROMPT})
        return True
    result = clock_time()
    record_tool("get_time", {}, result)
    print(f"[dates] forced get_time -> {result}", flush=True)
    return result

# Retry get_date once, then read the calendar in Python if still missing
def force_get_date(prompt, messages, message, already_retried, record_tool):
    if not already_retried:
        print("[dates] missing get_date, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": DATE_RETRY_PROMPT})
        return True
    result = calendar_date()
    record_tool("get_date", {}, result)
    print(f"[dates] forced get_date -> {result}", flush=True)
    return result

# Retry get_day once, then read the weekday in Python if still missing
def force_get_day(prompt, messages, message, already_retried, record_tool):
    if not already_retried:
        print("[dates] missing get_day, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": DAY_RETRY_PROMPT})
        return True
    result = calendar_day()
    record_tool("get_day", {}, result)
    print(f"[dates] forced get_day -> {result}", flush=True)
    return result

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
def answer_date_math(prompt, record_tool, conversation_history, trim_conversation_history):
    expression, target = date_math_expression(prompt)
    if expression is None:
        return None
    result = maths.run_calculate(expression)
    record_tool("calculate", {"expression": expression}, result)
    reply = speak_date_math_result(result, target)
    remember_date_math_turn(prompt, expression, result, reply, conversation_history, trim_conversation_history)
    return reply

# Answer which year the last calculate date expression used
def answer_prior_calculate_year(prompt, conversation_history, remember_exchange, parse_tool_arguments):
    expression = last_history_calculate_expression(conversation_history, parse_tool_arguments)
    if not expression:
        return None
    match = re.search(r"\bdate\s*\(\s*(\d{4})", expression)
    if not match:
        return None
    reply = f"I used the year {match.group(1)}."
    remember_exchange(prompt, reply)
    return reply

# Answer with the last calculate day-count result from history
def answer_prior_calculate_result(prompt, conversation_history, remember_exchange):
    result = last_history_calculate_result(conversation_history)
    if result is None:
        return None
    reply = f"The number is {result}."
    remember_exchange(prompt, reply)
    return reply

# Retry date math once, then compute it in Python if still missing
def force_date_calculate(prompt, messages, message, already_retried, record_tool):
    expression, target = date_math_expression(prompt)

    # First miss, ask the model again with an explicit date expression
    if not already_retried:
        print("[dates] missing date math, retrying", flush=True)
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
    result = maths.run_calculate(expression)
    record_tool("calculate", {"expression": expression}, result)
    print(f"[dates] forced date calculate -> {result}", flush=True)
    return speak_date_math_result(result, target)

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

# Keep a date-math tool call and spoken reply in conversation history
def remember_date_math_turn(prompt, expression, result, reply, conversation_history, trim_conversation_history):
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
def last_history_calculate_expression(conversation_history, parse_tool_arguments):
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
def last_history_calculate_result(conversation_history):
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

# Main
if __name__ == "__main__":
    main()
