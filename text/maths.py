#!/usr/bin/env python3

# Safe Python math and date expression evaluation for the calculate tool

# Imports
import ast
import math
import re
from datetime import date

# Config
MATH_RETRY_PROMPT = "Do not guess. Call calculate with a Python math expression now, then answer using only the tool result."
DATE_ATTRS = ("days", "year", "month", "day")
MONTH_LABELS = ("", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
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

# Tools the local model can call for math
TOOLS = [
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
]

# Return today's date for calculate expressions
def math_today():
    return date.today()

# Tiny math-only environment for calculate expressions
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
    "today": math_today,
}

# Main
def main():
    # Evaluate a default expression when run with no args
    print(run_calculate("17 * 43"))

# Return true when the question needs the Python math tool
def needs_math_tool(prompt):
    text = prompt.lower()
    if "volume" in text:
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

# Main
if __name__ == "__main__":
    main()
